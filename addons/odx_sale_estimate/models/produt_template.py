from odoo import _, api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    disable_order_line_sequence = fields.Boolean(String='Disable Order Line Sequence')
