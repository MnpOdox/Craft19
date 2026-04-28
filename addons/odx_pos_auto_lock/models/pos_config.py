from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    auto_lock_enabled = fields.Boolean(
        string="Auto Lock",
        default=True,
        help="Automatically lock the POS screen after no cashier activity.",
    )
    auto_lock_minutes = fields.Integer(
        string="Lock After",
        default=5,
        help="Number of idle minutes before the POS screen locks.",
    )

    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        # In Odoo 19 an empty list means "load all fields".
        # Keep that behavior so core fields such as use_pricelist are not dropped.
        if not fields_list:
            return fields_list
        for field_name in ("auto_lock_enabled", "auto_lock_minutes"):
            if field_name not in fields_list:
                fields_list.append(field_name)
        return fields_list
