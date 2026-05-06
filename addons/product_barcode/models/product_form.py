# -*- coding: utf-8 -*-
#############################################################################
#
#    Cybrosys Technologies Pvt. Ltd.
#
#    Copyright (C) 2020-TODAY Cybrosys Technologies(<https://www.cybrosys.com>).
#    Author: Niyas Raphy and Sreejith P (odoo@cybrosys.com)
#
#    You can modify it under the terms of the GNU AFFERO
#    GENERAL PUBLIC LICENSE (AGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU AFFERO GENERAL PUBLIC LICENSE (AGPL v3) for more details.
#
#    You should have received a copy of the GNU AFFERO GENERAL PUBLIC LICENSE
#    (AGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
#############################################################################

from odoo import api, models


class ProductAutoBarcode(models.Model):
    _inherit = 'product.product'

    def _next_auto_barcode(self):
        """Return the next generic barcode value.

        The barcode field itself is plain text in Odoo. We keep the generated
        value sequence-based so it works cleanly with Code128 printing without
        any EAN13 checksum or length requirements.
        """
        return self.env['ir.sequence'].next_by_code('product.product.craft')

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        for product in products:
            if not product.barcode:
                product.barcode = product._next_auto_barcode()
        return products


class ProductTemplateAutoBarcode(models.Model):
    _inherit = 'product.template'

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        for template in templates:
            if not template.barcode:
                template.barcode = self.env['product.product']._next_auto_barcode()
        return templates


# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
