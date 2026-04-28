# -*- coding: utf-8 -*-

from odoo import fields, models, api
import ast


class ResPartner(models.Model):
    _inherit = 'res.partner'
    location_link = fields.Char(string="Location")
    house_name = fields.Char(string="House name")
    district = fields.Char(string="District")
    pincode = fields.Char(string="pincode")

    country_code = fields.Char(string='Country Code')

    @api.onchange('country_id')
    def get_country_code(self):
        self.country_code = "+" + str(self.country_id.phone_code)



    @api.model
    def create_from_ui(self, partner):
        """ create or modify a partner from the point of sale ui.
            partner contains the partner's fields. """

        country_id = partner.get('country_id')
        country = self.env['res.country'].sudo().search([('id', '=', country_id)], limit=1)

        partner['country_code'] = '+' + str(country.phone_code)

        print('pppp',partner)
        # image is a dataurl, get the data after the comma
        if partner.get('image_1920'):
            partner['image_1920'] = partner['image_1920'].split(',')[1]
        partner_id = partner.pop('id', False)

        if partner_id:  # Modifying existing partner
            self.browse(partner_id).write(partner)

        else:
            partner_id = self.create(partner).id
        return partner_id

    @api.model
    def _load_pos_data_fields(self, config):
        result = super()._load_pos_data_fields(config)
        # In Odoo 19 an empty list means "load all fields".
        # Keep that behavior to avoid dropping required core fields.
        if not result:
            return result
        for name in ("location_link", "house_name", "district", "pincode", "country_code"):
            if name not in result:
                result.append(name)
        return result

