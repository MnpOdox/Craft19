from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    pos_cash_move_head_ids = fields.Many2many(
        "book.head",
        string="Cash In/Out Heads",
        domain=[("cash", "=", True)],
        help="Only these Cash Book heads are available when entering Cash In/Out in this POS.",
    )
