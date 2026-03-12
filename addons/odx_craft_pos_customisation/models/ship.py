from odoo import api, fields, models,_


class Ship(models.Model):
    _name = 'shipment.ship'
    _inherit = ['pos.load.mixin']
    _description = "Shipment ship"

    name = fields.Char(string='Name', required=True)

    @api.model
    def _load_pos_data_fields(self, config):
        return ["id", "name"]

