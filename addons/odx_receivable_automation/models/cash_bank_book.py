from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ReceivablePaymentMixin(models.AbstractModel):
    _name = "receivable.payment.mixin"
    _description = "Receivable Payment Synchronization"

    _receivable_source_type = False
    _receivable_source_field = False

    def _prepare_receivable_payment_vals(self):
        self.ensure_one()
        book = self.env["receivable.book"]._get_or_create_automation_book(
            self.partner_id, self.company_id
        )
        return {
            "receivable_id": book.id,
            "date": self.date,
            "description": self.description or self.head_id.head_name,
            "amount": abs(self.amount),
            "company_id": self.company_id.id,
            "source_type": self._receivable_source_type,
            self._receivable_source_field: self.id,
        }

    def _sync_receivable_payment(self):
        if self.env.context.get("skip_receivable_payment_sync"):
            return
        line_model = self.env["receivable.book.line"].sudo()
        for record in self:
            line = record.sudo().receivable_line_id
            enabled = bool(record.head_id.auto_receivable_payment)
            if not enabled:
                if line:
                    record.sudo().with_context(skip_receivable_payment_sync=True).write({"receivable_line_id": False})
                    line.unlink()
                continue
            if not record.partner_id:
                raise ValidationError(_("Select a Partner for this Receivable payment."))
            if record.amount >= 0:
                raise ValidationError(_("A Receivable payment must be entered as a negative Cash/Bank amount."))
            vals = record._prepare_receivable_payment_vals()
            if line:
                line.write(vals)
            else:
                line = line_model.create(vals)
                record.sudo().with_context(skip_receivable_payment_sync=True).write({"receivable_line_id": line.id})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_receivable_payment()
        return records

    def write(self, vals):
        result = super().write(vals)
        if any(key in vals for key in ("head_id", "partner_id", "date", "description", "amount", "company_id")):
            self._sync_receivable_payment()
        return result

    def unlink(self):
        lines = self.sudo().mapped("receivable_line_id")
        result = super().unlink()
        if lines:
            lines.unlink()
        return result


class CashBookLine(models.Model):
    _name = "cash.book.line"
    _inherit = ["cash.book.line", "receivable.payment.mixin"]
    _receivable_source_type = "cash_payment"
    _receivable_source_field = "cash_book_line_id"

    partner_id = fields.Many2one(
        "res.partner", string="Partner",
        domain=[("has_confirmed_receivable_book", "=", True)],
    )
    receivable_line_id = fields.Many2one(
        "receivable.book.line", string="Receivable Entry",
        readonly=True, copy=False, ondelete="set null",
        groups="odx_books.group_receivable_book_user,odx_books.group_book_manager",
    )


class BankBookLine(models.Model):
    _name = "bank.book.line"
    _inherit = ["bank.book.line", "receivable.payment.mixin"]
    _receivable_source_type = "bank_payment"
    _receivable_source_field = "bank_book_line_id"

    partner_id = fields.Many2one(
        "res.partner", string="Partner",
        domain=[("has_confirmed_receivable_book", "=", True)],
    )
    receivable_line_id = fields.Many2one(
        "receivable.book.line", string="Receivable Entry",
        readonly=True, copy=False, ondelete="set null",
        groups="odx_books.group_receivable_book_user,odx_books.group_book_manager",
    )
