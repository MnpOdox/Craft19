# Copyright (C) 2017 - Today: GRAP (http://www.grap.coop)
# @author: Sylvain LE GAL (https://twitter.com/legalsylvain)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import api, fields, models
from odoo.tools import float_is_zero


class PosOrder(models.Model):
    _inherit = "pos.order"

    # Columns Section
    margin = fields.Float(
        "Margin",
        compute="_compute_margin",
        store=True,
        digits="Product Price",
        help="It gives profitability by calculating the difference between"
        " the Unit Price and the cost price.",
    )
    margin_percent = fields.Float(
        "Margin (%)",
        compute="_compute_margin",
        store=True,
        digits=(12, 4),
    )

    # Compute Section
    @api.depends("lines.margin")
    def _compute_margin(self):
        for order in self:
            margin = sum(order.mapped("lines.margin"))
            amount_untaxed = sum(order.lines.mapped("price_subtotal"))
            if order.is_refund:
                amount_untaxed *= -1
            order.margin = margin
            order.margin_percent = (
                margin / amount_untaxed
                if order.currency_id
                and not float_is_zero(
                    amount_untaxed, precision_rounding=order.currency_id.rounding
                )
                else 0.0
            )
