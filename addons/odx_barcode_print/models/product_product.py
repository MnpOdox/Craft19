from odoo import fields, models, api


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def button_action_wizard(self):
        view_id = self.env.ref('odx_barcode_print.barcode_wizard_view').id
        return {

            'name': "Multi Product barcode",
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'odx.barcode.wizard',
            'target': 'new',
            'view_id': [(view_id, 'form')],
        }
