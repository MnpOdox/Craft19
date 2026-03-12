from odoo import fields, models

class StockInventoryLine(models.Model):
    _inherit = 'stock.inventory.line'

    barcode = fields.Char(string='Barcode', related='product_id.barcode', store=True)
    product_id = fields.Many2one('product.product')


