from  odoo import api, fields, models


class SalePaymentMethod(models.Model):
    _name = 'sale.payment.method'
    _description = 'Sale Payment Method'

    name = fields.Char(string='Name', required=True)