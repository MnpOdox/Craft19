from odoo import models, fields,api

class TrackingUpdate(models.TransientModel):
    _name = 'tracking.update.wizard'
    _description = 'Tracking Bulk Update'

    start_date = fields.Date(string="Start Date", required=True, default=fields.Datetime.today())
    end_date = fields.Date(string="End Date", required=True, default=fields.Datetime.today())

    tracking_line_ids = fields.One2many('tracking.update.line','tracking_id')




    @api.onchange('start_date','end_date')
    def pos_order(self):

        start_date = self.start_date
        end_date = self.end_date
        order_data = [(5,0,0)]

        orders = self.env['pos.order'].search([('date_order','>=',start_date),('date_order','<=',end_date),('online_order','=',True)])
        if orders:
            for order in orders:
                order_details = {
                        "pos_order_id": order.id,
                        "partner_id": order.partner_id.id,
                        "order_number": order.pos_reference,
                        "tracking_number": order.tracking_number,
                        "courier_id": order.courier_id.id,
                    }
                order_data.append((0,0,order_details))
            self.tracking_line_ids = order_data


    def tracking_update(self):

        for track in self.tracking_line_ids:
            tracking_number = track.tracking_number
            courier = track.courier_id.id
            orders = self.env['pos.order'].sudo().browse([(track.pos_order_id.id)])
            orders.tracking_number = tracking_number
            orders.courier_id = courier





class TrackingUpdateLine(models.TransientModel):
    _name = 'tracking.update.line'
    _description = ""

    tracking_id = fields.Many2one('tracking.update.wizard', string="Tracking")
    partner_id = fields.Many2one('res.partner', string="Customer Name")
    order_number = fields.Char(string="Order Number")
    courier_id = fields.Many2one('shipment.ship',string="Courier")
    tracking_number = fields.Char(string="Tracking Number")
    pos_order_id = fields.Many2one('pos.order',string='Id')








