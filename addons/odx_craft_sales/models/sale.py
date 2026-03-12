from odoo import models, fields, api, _

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    total_discount = fields.Float(string="Total Discount", compute="_compute_total_discount")
    sub_total_amount = fields.Float(string="Sub Total", compute="_compute_subtotal_amount")

    # COMPUTING SUB TOTAL AMOUNT BASED ON QUANTITY,UNIT PRICE(MRP)
    @api.depends('order_line.product_uom_qty', 'order_line.price_unit')
    def _compute_subtotal_amount(self):
        for rec in self:
            rec.sub_total_amount = sum([(line.product_uom_qty * line.price_unit) for line in rec.order_line])

    # COMPUTING TOTAL DISCOUNT AMOUNT
    @api.depends('order_line.discount')
    def _compute_total_discount(self):
        for rec in self:
            rec.total_discount = sum(rec.order_line.mapped('disc_amount'))



class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    unit_disc_price = fields.Float(string="Unit Price",compute="_compute_unit_discount_price")
    disc_amount = fields.Float(string="Discount Amount",compute="_compute_disc_amount")

    # COMPUTING EACH LINE DISCOUNTED UNIT PRICE  BASED ON DISCOUNT,UNIT PRICE(MRP) OF ONE PRODUCT
    @api.depends('discount', 'product_uom_qty','price_unit')
    def _compute_unit_discount_price(self):
        for rec in self:
                # rec.unit_disc_price = rec.price_unit - (rec.discount / 100)
                if rec.price_unit:
                    discount_amount = rec.price_unit * rec.discount/100
                    rec.unit_disc_price = rec.price_unit - discount_amount
                else:
                    rec.unit_disc_price = 0.00

    # COMPUTING DISCOUNT AMOUNT
    @api.depends('discount', 'price_unit')
    def _compute_disc_amount(self):
        for rec in self:
            rec.disc_amount = rec.product_uom_qty*rec.price_unit - rec.price_subtotal






