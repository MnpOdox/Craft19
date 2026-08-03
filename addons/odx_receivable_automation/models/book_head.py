from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class BookHead(models.Model):
    _inherit = "book.head"

    receivable_payment_effect = fields.Selection([
        ("none", "No Receivable Entry"),
        ("payable_settlement", "Payable / Salary Settlement"),
        ("customer_receipt", "Customer Credit Receipt"),
    ], string="Receivable Payment Effect", default="none", required=True,
        help=(
            "Payable / Salary Settlement requires a negative Cash/Bank amount and "
            "creates a positive Receivable line. Customer Credit Receipt requires "
            "a positive Cash/Bank amount and creates a negative Receivable line."
        ),
    )
    auto_receivable_expense = fields.Boolean(
        string="Update Receivable on Expense",
        help="Create a negative Receivable line when an Expense uses this head.",
    )

    @api.constrains("auto_expense", "auto_receivable_expense")
    def _check_receivable_expense_configuration(self):
        for head in self:
            if head.auto_expense and head.auto_receivable_expense:
                raise ValidationError(_(
                    "A head cannot both auto-create an Expense from Cash/Bank and "
                    "create a Receivable from Expense; this would double-post the expense."
                ))
