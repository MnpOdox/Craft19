from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ExpenseBook(models.Model):
    _inherit = "expense.book"

    partner_id = fields.Many2one(
        "res.partner", string="Partner",
        domain=[("has_confirmed_receivable_book", "=", True)],
    )
    receivable_line_id = fields.Many2one(
        "receivable.book.line", string="Receivable Entry",
        readonly=True, copy=False, ondelete="set null",
        groups="odx_books.group_receivable_book_user,odx_books.group_book_manager",
    )

    def _prepare_receivable_expense_vals(self):
        self.ensure_one()
        book = self.env["receivable.book"]._get_confirmed_automation_book(
            self.partner_id, self.company_id
        )
        return {
            "receivable_id": book.id,
            "date": self.date,
            "description": self.description or self.head_id.head_name,
            "amount": -abs(self.amount),
            "company_id": self.company_id.id,
            "source_type": "salary_expense",
            "expense_book_id": self.id,
        }

    def _sync_receivable_expense(self):
        if self.env.context.get("skip_receivable_expense_sync"):
            return
        line_model = self.env["receivable.book.line"].sudo()
        for expense in self:
            line = expense.sudo().receivable_line_id
            if not expense.head_id.auto_receivable_expense:
                if line:
                    expense.sudo().with_context(skip_receivable_expense_sync=True).write({
                        "receivable_line_id": False,
                    })
                    line.unlink()
                continue
            if not expense.partner_id:
                raise ValidationError(_("Select a Partner for this Receivable expense."))
            if expense.amount <= 0:
                raise ValidationError(_("A Receivable expense amount must be positive."))
            vals = expense._prepare_receivable_expense_vals()
            if line:
                line.write(vals)
            else:
                line = line_model.create(vals)
                expense.sudo().with_context(skip_receivable_expense_sync=True).write({
                    "receivable_line_id": line.id,
                })

    @api.model_create_multi
    def create(self, vals_list):
        expenses = super().create(vals_list)
        expenses._sync_receivable_expense()
        return expenses

    def write(self, vals):
        result = super().write(vals)
        if any(key in vals for key in ("head_id", "partner_id", "date", "description", "amount", "company_id")):
            self._sync_receivable_expense()
        return result

    def unlink(self):
        lines = self.sudo().mapped("receivable_line_id")
        result = super().unlink()
        if lines:
            lines.unlink()
        return result
