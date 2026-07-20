from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PosSession(models.Model):
    _inherit = "pos.session"

    pos_book_synced = fields.Boolean(string="Books Synchronized", readonly=True, copy=False)
    pos_book_sync_date = fields.Datetime(string="Book Synchronization Date", readonly=True, copy=False)
    pos_cash_book_line_ids = fields.One2many("cash.book.line", "pos_session_id", string="Cash Book Lines")
    pos_bank_book_line_ids = fields.One2many("bank.book.line", "pos_session_id", string="Bank Book Lines")
    pos_book_line_count = fields.Integer(compute="_compute_pos_book_line_count")
    pos_cash_book_line_count = fields.Integer(compute="_compute_pos_book_line_count")
    pos_bank_book_line_count = fields.Integer(compute="_compute_pos_book_line_count")

    @api.depends("pos_cash_book_line_ids", "pos_bank_book_line_ids")
    def _compute_pos_book_line_count(self):
        for session in self:
            session.pos_cash_book_line_count = len(session.pos_cash_book_line_ids)
            session.pos_bank_book_line_count = len(session.pos_bank_book_line_ids)
            session.pos_book_line_count = session.pos_cash_book_line_count + session.pos_bank_book_line_count

    @api.model
    def _load_pos_data_models(self, config):
        result = super()._load_pos_data_models(config)
        if "book.head" not in result:
            result.append("book.head")
        return result

    def _get_pos_book_date(self):
        self.ensure_one()
        closing_datetime = self.stop_at or fields.Datetime.now()
        return fields.Date.to_date(fields.Datetime.context_timestamp(self, closing_datetime))

    def _get_pos_book_for_method(self, payment_method, book_date):
        self.ensure_one()
        model_name = "cash.book" if payment_method.type == "cash" else "bank.book"
        books = self.env[model_name].sudo().search([
            ("company_id", "=", self.company_id.id),
            ("state", "=", "confirm"),
            ("start_date", "<=", book_date),
            ("end_date", ">=", book_date),
            ("pos_payment_method_ids", "in", payment_method.id),
        ])
        if not books:
            raise UserError(
                _("Cannot close %(session)s: payment method %(method)s is not assigned to an active %(book)s Book covering %(date)s.",
                  session=self.display_name, method=payment_method.display_name,
                  book=payment_method.type.title(), date=book_date)
            )
        if len(books) > 1:
            raise UserError(
                _("Cannot close %(session)s: payment method %(method)s is assigned to more than one active book for %(date)s.",
                  session=self.display_name, method=payment_method.display_name, date=book_date)
            )
        if not books.pos_sales_head_id:
            raise UserError(
                _("Cannot close %(session)s: configure a POS Sales Head on %(book)s.",
                  session=self.display_name, book=books.display_name)
            )
        return books

    def _get_pos_book_payments(self):
        self.ensure_one()
        orders = self._get_closed_orders().filtered(lambda order: order.state != "cancel")
        return orders.payment_ids.filtered(
            lambda payment: payment.payment_method_id.type != "pay_later"
            and not payment.payment_method_id.pos_book_excluded
        )

    def _validate_pos_book_setup(self):
        for session in self:
            book_date = session._get_pos_book_date()
            for method in session._get_pos_book_payments().mapped("payment_method_id"):
                session._get_pos_book_for_method(method, book_date)
            missing_head_moves = session.statement_line_ids.filtered(
                lambda line: not line.pos_book_head_id and line.payment_ref
                and (line.payment_ref.startswith(session.name + "-") or line.payment_ref.startswith(session.name + " -"))
            )
            if missing_head_moves:
                raise UserError(
                    _("Cannot close %(session)s: every POS Cash In/Out must have a Cash Book head. Missing on: %(moves)s",
                      session=session.display_name,
                      moves=", ".join(missing_head_moves.mapped("payment_ref")))
                )

    def action_pos_session_close(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        sessions_to_close = self.filtered(lambda session: session.state != "closed")
        sessions_to_close._validate_pos_book_setup()
        result = super().action_pos_session_close(balancing_account, amount_to_balance, bank_payment_method_diffs)
        sessions_to_close.filtered(lambda session: session.state == "closed")._sync_pos_book_entries()
        return result

    def _sync_pos_book_entries(self):
        for session in self:
            if session.state != "closed":
                raise UserError(_("POS Book entries can only be generated for a closed session."))
            book_date = session._get_pos_book_date()
            payments = session._get_pos_book_payments()
            for method in payments.mapped("payment_method_id"):
                amount = sum(payments.filtered(lambda payment: payment.payment_method_id == method).mapped("amount"))
                if session.currency_id.is_zero(amount):
                    continue
                book = session._get_pos_book_for_method(method, book_date)
                line_model = "cash.book.line" if method.type == "cash" else "bank.book.line"
                parent_field = "name_id"
                existing = self.env[line_model].sudo().search_count([
                    ("pos_session_id", "=", session.id),
                    ("pos_payment_method_id", "=", method.id),
                    ("pos_source_type", "=", "sale"),
                ])
                if not existing:
                    self.env[line_model].sudo().with_context(pos_book_sync=True).create({
                        parent_field: book.id,
                        "date": book_date,
                        "head_id": book.pos_sales_head_id.id,
                        "description": _("%(session)s - %(method)s Sales", session=session.name, method=method.name),
                        "amount": amount,
                        "company_id": session.company_id.id,
                        "pos_session_id": session.id,
                        "pos_payment_method_id": method.id,
                        "pos_source_type": "sale",
                    })

            cash_moves = session.statement_line_ids.filtered(
                lambda move: move.pos_book_head_id and not move.pos_book_head_id.pos_drawer_transfer
            )
            cash_methods = session.payment_method_ids.filtered(lambda method: method.type == "cash")
            cash_book = (
                session._get_pos_book_for_method(cash_methods[:1], book_date)
                if cash_moves and cash_methods else False
            )
            for move in cash_moves:
                if not cash_book:
                    raise UserError(_("Cannot post POS Cash In/Out because no cash payment method is configured."))
                if self.env["cash.book.line"].sudo().search_count([("pos_statement_line_id", "=", move.id)]):
                    continue
                self.env["cash.book.line"].sudo().with_context(pos_book_sync=True).create({
                    "name_id": cash_book.id,
                    "date": move.date or book_date,
                    "head_id": move.pos_book_head_id.id,
                    "description": move.payment_ref,
                    "amount": move.amount,
                    "company_id": session.company_id.id,
                    "pos_session_id": session.id,
                    "pos_statement_line_id": move.id,
                    "pos_source_type": "cash_in" if move.amount > 0 else "cash_out",
                })
            session.sudo().write({"pos_book_synced": True, "pos_book_sync_date": fields.Datetime.now()})

    def action_rebuild_pos_book_entries(self):
        if not self.env.user.has_group("odx_books.group_book_manager"):
            raise UserError(_("Only a Book Manager can rebuild POS Book entries."))
        for session in self:
            session.pos_cash_book_line_ids.with_context(pos_book_rebuild=True).unlink()
            session.pos_bank_book_line_ids.with_context(pos_book_rebuild=True).unlink()
            session.write({"pos_book_synced": False, "pos_book_sync_date": False})
            session._validate_pos_book_setup()
            session._sync_pos_book_entries()
        return True

    def action_open_pos_cash_book_lines(self):
        self.ensure_one()
        list_view = self.env.ref("odx_pos_books.view_pos_cash_book_line_list")
        form_view = self.env.ref("odx_pos_books.view_pos_cash_book_line_form")
        return {
            "type": "ir.actions.act_window",
            "name": _("POS Cash Book Lines"),
            "res_model": "cash.book.line",
            "view_mode": "list,form",
            "views": [(list_view.id, "list"), (form_view.id, "form")],
            "domain": [("pos_session_id", "=", self.id)],
            "context": {"create": False},
        }

    def action_open_pos_bank_book_lines(self):
        self.ensure_one()
        list_view = self.env.ref("odx_pos_books.view_pos_bank_book_line_list")
        form_view = self.env.ref("odx_pos_books.view_pos_bank_book_line_form")
        return {
            "type": "ir.actions.act_window",
            "name": _("POS Bank Book Lines"),
            "res_model": "bank.book.line",
            "view_mode": "list,form",
            "views": [(list_view.id, "list"), (form_view.id, "form")],
            "domain": [("pos_session_id", "=", self.id)],
            "context": {"create": False},
        }

    def _prepare_account_bank_statement_line_vals(self, session, sign, amount, reason, partner_id, extras):
        vals = super()._prepare_account_bank_statement_line_vals(session, sign, amount, reason, partner_id, extras)
        head_id = extras.get("pos_book_head_id")
        if head_id:
            head = self.env["book.head"].browse(head_id).exists()
            if not head or head not in session.config_id.pos_cash_move_head_ids:
                raise UserError(_("Select a Cash Book head enabled for this Point of Sale."))
            vals["pos_book_head_id"] = head.id
        return vals

    def try_cash_in_out(self, _type, amount, reason, partner_id, extras):
        head_id = extras.get("pos_book_head_id")
        if not head_id:
            raise UserError(_("Select a Cash Book head before confirming Cash In/Out."))
        head = self.env["book.head"].browse(head_id).exists()
        if not head or any(head not in session.config_id.pos_cash_move_head_ids for session in self):
            raise UserError(_("Select a Cash Book head enabled for this Point of Sale."))
        return super().try_cash_in_out(_type, amount, head.head_name, partner_id, extras)
