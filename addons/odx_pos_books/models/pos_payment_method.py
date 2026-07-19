from odoo import fields, models


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    pos_book_excluded = fields.Boolean(
        string="Exclude from POS Books",
        help="Do not transfer this payment method to a Cash or Bank Book at session closing.",
    )
