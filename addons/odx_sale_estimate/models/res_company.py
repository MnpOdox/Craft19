from odoo import fields, models, api


class ResCompany(models.Model):
    _inherit = 'res.company'

    automatic_delivery_validation = fields.Boolean(string='Automatic Delivery Validation')