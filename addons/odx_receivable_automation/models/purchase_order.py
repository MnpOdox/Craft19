from odoo import api, fields, models, _


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    receivable_line_ids = fields.One2many(
        "receivable.book.line", "purchase_order_id",
        string="Receivable Lines", readonly=True, copy=False,
        groups="odx_books.group_receivable_book_user,odx_books.group_book_manager",
    )
    receivable_line_count = fields.Integer(
        compute="_compute_receivable_line_count",
        groups="odx_books.group_receivable_book_user,odx_books.group_book_manager",
    )

    @api.depends("receivable_line_ids")
    def _compute_receivable_line_count(self):
        for order in self:
            order.receivable_line_count = len(order.receivable_line_ids)

    def _prepare_receivable_purchase_vals(self):
        self.ensure_one()
        book = self.env["receivable.book"]._get_or_create_automation_book(
            self.partner_id, self.company_id
        )
        order_date = fields.Date.to_date(self.date_order) or fields.Date.context_today(self)
        return {
            "receivable_id": book.id,
            "date": order_date,
            "description": _("Purchase %(order)s", order=self.name),
            "amount": -abs(self.amount_total),
            "company_id": self.company_id.id,
            "source_type": "purchase",
            "purchase_order_id": self.id,
        }

    def _sync_receivable_purchase(self):
        line_model = self.env["receivable.book.line"].sudo()
        for order in self:
            line = line_model.search([("purchase_order_id", "=", order.id)], limit=1)
            if order.state not in ("purchase", "done"):
                if line:
                    line.unlink()
                continue
            vals = order._prepare_receivable_purchase_vals()
            if line:
                line.write(vals)
            else:
                line_model.create(vals)

    def button_confirm(self):
        result = super().button_confirm()
        self._sync_receivable_purchase()
        return result

    def button_approve(self, force=False):
        result = super().button_approve(force=force)
        self._sync_receivable_purchase()
        return result

    def button_cancel(self):
        result = super().button_cancel()
        self._sync_receivable_purchase()
        return result

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get("skip_receivable_purchase_sync") and any(
            key in vals for key in ("state", "partner_id", "company_id", "date_order", "order_line")
        ):
            self._sync_receivable_purchase()
        return result

    def action_open_receivable_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Receivable Lines"),
            "res_model": "receivable.book.line",
            "view_mode": "list,form",
            "domain": [("purchase_order_id", "=", self.id)],
            "context": {"create": False},
        }


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _sync_order_receivable_purchases(self, orders=None):
        (orders or self.mapped("order_id")).filtered(
            lambda order: order.state in ("purchase", "done")
        )._sync_receivable_purchase()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._sync_order_receivable_purchases()
        return lines

    def write(self, vals):
        result = super().write(vals)
        if any(key in vals for key in ("product_qty", "price_unit", "tax_ids", "discount")):
            self._sync_order_receivable_purchases()
        return result

    def unlink(self):
        orders = self.mapped("order_id")
        result = super().unlink()
        self._sync_order_receivable_purchases(orders=orders)
        return result
