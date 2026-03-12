# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def send_sms(self):

        for rec in self:

            template = self.env['send_sms'].search([('template_name', '=', 'send_otp')], limit=1)
            gateway = template.gateway_id
            msg = template.sms_html
            msg.replace('673253')

            try:
                gateway_id = gateway.send_sms_link(template.sms_html, rec.partner_id.phone, self.id, self._name, gateway)

            except:
                pass
