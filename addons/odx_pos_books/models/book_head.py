from odoo import api, fields, models


class BookHead(models.Model):
    _name = "book.head"
    _inherit = ["book.head", "pos.load.mixin"]

    pos_drawer_transfer = fields.Boolean(
        string="POS Drawer Transfer",
        help=(
            "Use for cash transferred between the POS drawer and the manager. Cash Out "
            "reduces the drawer and Cash In replenishes it. Neither movement is posted "
            "again to the Cash Book."
        ),
    )

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [("id", "in", config.pos_cash_move_head_ids.ids)]

    @api.model
    def _load_pos_data_fields(self, config):
        return ["id", "head_name", "cash", "auto_expense", "pos_drawer_transfer"]
