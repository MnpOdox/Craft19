from odoo import models


class ResCompany(models.Model):
    _inherit = "res.company"

    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        if "street2" not in fields:
            fields.append("street2")
        return fields
