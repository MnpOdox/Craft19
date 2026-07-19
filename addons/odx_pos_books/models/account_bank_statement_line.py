from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    pos_book_head_id = fields.Many2one(
        "book.head",
        string="Cash Book Head",
        copy=False,
        domain=[("cash", "=", True)],
        help="Cash Book head selected when this POS Cash In/Out was entered.",
    )

    @api.constrains("pos_book_head_id")
    def _check_pos_book_head(self):
        for line in self:
            if line.pos_book_head_id and not line.pos_book_head_id.cash:
                raise ValidationError(_("The selected head is not enabled for the Cash Book."))
