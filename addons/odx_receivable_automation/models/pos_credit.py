from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    auto_receivable_credit_sale = fields.Boolean(
        string="Update Receivable for Credit Sales",
        help="Post this payment method's amount to the customer's Receivable Book at session closing.",
    )


class PosOrder(models.Model):
    _inherit = "pos.order"

    credit_receivable_line_ids = fields.One2many(
        "receivable.book.line", "pos_order_id",
        string="Credit Receivable Lines", readonly=True, copy=False,
        groups="odx_books.group_receivable_book_user,odx_books.group_book_manager",
    )

    def _get_credit_receivable_amount(self):
        self.ensure_one()
        payments = self.payment_ids.filtered(
            lambda payment: payment.payment_method_id.auto_receivable_credit_sale
        )
        return sum(payments.mapped("amount"))

    def _get_confirmed_credit_receivable_book(self):
        self.ensure_one()
        if not self.partner_id:
            return False
        return self.env["receivable.book"].sudo().search([
            ("partner_id", "=", self.partner_id.id),
            ("company_id", "=", self.company_id.id),
            ("state", "=", "confirm"),
        ], order="id desc", limit=1)

    def _validate_credit_receivable_setup(self):
        errors = []
        for order in self:
            amount = order._get_credit_receivable_amount()
            if order.currency_id.is_zero(amount):
                continue
            if not order.partner_id:
                errors.append(_("%(order)s: select a customer", order=order.name))
            elif not order._get_confirmed_credit_receivable_book():
                errors.append(_(
                    "%(order)s: %(customer)s has no Confirmed Receivable Book",
                    order=order.name,
                    customer=order.partner_id.display_name,
                ))
        if errors:
            raise UserError(_(
                "Cannot close the POS session because Credit Receivable setup is incomplete:\n%(errors)s",
                errors="\n".join(errors),
            ))

    def _sync_credit_receivable_lines(self):
        line_model = self.env["receivable.book.line"].sudo()
        for order in self:
            line = line_model.search([("pos_order_id", "=", order.id)], limit=1)
            amount = order._get_credit_receivable_amount()
            should_exist = (
                order.state != "cancel"
                and order.session_id.state == "closed"
                and not order.currency_id.is_zero(amount)
            )
            if not should_exist:
                if line:
                    line.unlink()
                continue
            order._validate_credit_receivable_setup()
            book = order._get_confirmed_credit_receivable_book()
            order_date = fields.Date.to_date(
                fields.Datetime.context_timestamp(order, order.date_order)
            )
            vals = {
                "receivable_id": book.id,
                "date": order_date,
                "description": _("Credit Sale %(order)s", order=order.name),
                "amount": amount,
                "company_id": order.company_id.id,
                "source_type": "credit_sale",
                "pos_order_id": order.id,
            }
            if line:
                line.write(vals)
            else:
                line_model.create(vals)

    def write(self, vals):
        result = super().write(vals)
        if "partner_id" in vals:
            self.filtered(lambda order: order.session_id.state == "closed")._sync_credit_receivable_lines()
        return result


class PosPayment(models.Model):
    _inherit = "pos.payment"

    def _sync_closed_credit_orders(self, orders=None):
        (orders or self.mapped("pos_order_id")).filtered(
            lambda order: order.session_id.state == "closed"
        )._sync_credit_receivable_lines()

    @api.model_create_multi
    def create(self, vals_list):
        payments = super().create(vals_list)
        payments._sync_closed_credit_orders()
        return payments

    def write(self, vals):
        orders = self.mapped("pos_order_id")
        result = super().write(vals)
        if any(key in vals for key in ("payment_method_id", "amount", "pos_order_id")):
            self._sync_closed_credit_orders(orders | self.mapped("pos_order_id"))
        return result

    def unlink(self):
        orders = self.mapped("pos_order_id")
        result = super().unlink()
        self._sync_closed_credit_orders(orders)
        return result


class PosSession(models.Model):
    _inherit = "pos.session"

    credit_receivable_synced = fields.Boolean(
        string="Credit Receivables Synchronized", readonly=True, copy=False,
    )
    credit_receivable_sync_date = fields.Datetime(
        string="Credit Receivable Synchronization Date", readonly=True, copy=False,
    )
    pos_credit_receivable_line_ids = fields.One2many(
        "receivable.book.line", "pos_session_id",
        string="Credit Receivable Lines", readonly=True, copy=False,
        groups="odx_books.group_receivable_book_user,odx_books.group_book_manager",
    )
    pos_credit_receivable_line_count = fields.Integer(
        compute="_compute_pos_credit_receivable_line_count", compute_sudo=True,
        groups="odx_books.group_receivable_book_user,odx_books.group_book_manager",
    )

    @api.depends("pos_credit_receivable_line_ids")
    def _compute_pos_credit_receivable_line_count(self):
        for session in self:
            session.pos_credit_receivable_line_count = len(
                session.sudo().pos_credit_receivable_line_ids
            )

    def _get_credit_receivable_orders(self):
        self.ensure_one()
        return self._get_closed_orders().filtered(lambda order: order.state != "cancel")

    def _validate_credit_receivable_setup(self):
        for session in self:
            session._get_credit_receivable_orders()._validate_credit_receivable_setup()

    def _sync_credit_receivable_entries(self):
        for session in self:
            if session.state != "closed":
                raise UserError(_("Credit Receivable entries can only be generated for a closed POS session."))
            orders = session._get_credit_receivable_orders()
            orders._sync_credit_receivable_lines()
            stale_lines = self.env["receivable.book.line"].sudo().search([
                ("pos_session_id", "=", session.id),
                ("source_type", "=", "credit_sale"),
                ("pos_order_id", "not in", orders.ids),
            ])
            stale_lines.unlink()
            session.sudo().write({
                "credit_receivable_synced": True,
                "credit_receivable_sync_date": fields.Datetime.now(),
            })

    def action_pos_session_close(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        sessions_to_close = self.filtered(lambda session: session.state != "closed")
        sessions_to_close._validate_credit_receivable_setup()
        result = super().action_pos_session_close(
            balancing_account, amount_to_balance, bank_payment_method_diffs
        )
        sessions_to_close.filtered(
            lambda session: session.state == "closed"
        )._sync_credit_receivable_entries()
        return result

    def action_rebuild_pos_book_entries(self):
        result = super().action_rebuild_pos_book_entries()
        self._validate_credit_receivable_setup()
        self._sync_credit_receivable_entries()
        return result

    def action_open_pos_credit_receivable_lines(self):
        self.ensure_one()
        list_view = self.env.ref("odx_receivable_automation.view_receivable_automation_line_list")
        form_view = self.env.ref("odx_receivable_automation.view_receivable_automation_line_form")
        return {
            "type": "ir.actions.act_window",
            "name": _("POS Credit Receivable Lines"),
            "res_model": "receivable.book.line",
            "view_mode": "list,form",
            "views": [(list_view.id, "list"), (form_view.id, "form")],
            "domain": [("pos_session_id", "=", self.id), ("source_type", "=", "credit_sale")],
            "context": {"create": False},
        }
