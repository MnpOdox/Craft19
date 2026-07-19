from odoo import api, fields, models


class BookHead(models.Model):
    _name = "book.head"
    _inherit = ["book.head", "pos.load.mixin"]

    pos_drawer_transfer = fields.Boolean(
        string="POS Drawer Transfer",
        help=(
            "Use for the end-of-day Cash Out handed to the manager. The movement reduces "
            "the POS drawer but is not posted again to the Cash Book."
        ),
    )

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [("id", "in", config.pos_cash_move_head_ids.ids)]

    @api.model
    def _load_pos_data_fields(self, config):
        return ["id", "head_name", "cash", "auto_expense", "pos_drawer_transfer"]
