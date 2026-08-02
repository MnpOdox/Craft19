from odoo import fields, models


class BookHead(models.Model):
    _inherit = "book.head"

    auto_receivable_payment = fields.Boolean(
        string="Update Receivable on Payment",
        help=(
            "For an outgoing Cash/Bank line using this head, create a positive "
            "entry in the selected partner's Receivable Book."
        ),
    )
