from odoo import _, api, fields, models, tools

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sequence = fields.Integer(string='Sequence Number')

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    sale_sequence_number = fields.Integer(string='No.',compute="_compute_sale_order_line_sequence")
    
    @api.depends('sale_sequence_number')
    def _compute_sale_order_line_sequence(self):
        number = 1
        for record in self.order_id.order_line:
            record.sale_sequence_number = number
            number += 1
   
class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    sequence = fields.Char(string='Sequence Number')

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    purchase_sequence_number = fields.Integer(string='No.',compute="_compute_purchase_order_line_sequence")
    vendor_line_number = fields.Integer(string='Vendor Line No.')

    @api.depends('purchase_sequence_number')
    def _compute_purchase_order_line_sequence(self):
        number = 1
        for record in self.order_id.order_line:
            record.purchase_sequence_number = number
            number += 1

class AccountMove(models.Model):
    _inherit = 'account.move'

    sequence = fields.Char(string='Sequence Number')

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    invoice_sequence_number = fields.Char(string='No.',compute="_compute_invoice_line_sequence")

    @api.depends('invoice_sequence_number')
    def _compute_invoice_line_sequence(self):
        number = 1
        for record in self.move_id.invoice_line_ids:
            record.invoice_sequence_number = number
            number += 1



class StockMove(models.Model):
    _inherit = 'stock.move'

    sequence = fields.Char(string='Sequence Number')
    stock_move_sequence = fields.Integer(string='No.',compute='_compute_stock_line_sequence')
    # mrp_sequence_no = fields.Integer(string='No.',compute='_compute_mrp_line_sequence')

    @api.depends('stock_move_sequence')
    def _compute_stock_line_sequence(self):
        number = 1
        for record in self.picking_id.move_ids_without_package:
            record.stock_move_sequence = number
            number += 1

    # @api.depends('mrp_sequence_no')
    # def _compute_mrp_line_sequence(self):
    #     number = 1
    #     for record in self.raw_material_production_id.move_raw_ids:
    #         record.mrp_sequence_no = number
    #         number += 1



class PurchaseRequisition(models.Model):
    _inherit = 'purchase.requisition.line'

    purchase_requistion_sequence = fields.Integer(string='No.',compute='_compute_purchase_requistion_sequence') 

    @api.depends('purchase_requistion_sequence')
    def _compute_purchase_requistion_sequence(self):
        number = 1
        for record in self.requisition_id.line_ids:
            record.purchase_requistion_sequence = number
            number += 1

