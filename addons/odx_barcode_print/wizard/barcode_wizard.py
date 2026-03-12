from odoo import fields, models, api
from odoo.exceptions import UserError as Warning


class OdxBarcodeWizard(models.TransientModel):
    _name = "odx.barcode.wizard"
    _description = "Barcode wizard"

    def barcode_multiple_product(self):
        print("abc")
        data = {

                'product_ids': self.multiple_products_ids,
                # 'company_id': self.company_id,
               }
        print(data)
        print(self.env.ref('odx_barcode_print.product_barcode_report').report_action(self))
        return self.env.ref('odx_barcode_print.product_barcode_report').report_action(self)

    multiple_products_ids = fields.Many2many("product.product", string='Select Products')
    # product_two = fields.Many2one("product.product", string='Second Product')
