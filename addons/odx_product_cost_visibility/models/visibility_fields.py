from odoo import fields, models


_MARGIN_GROUPS = "sales_team.group_sale_manager,base.group_system"


class SaleOrder(models.Model):
    _inherit = "sale.order"

    margin = fields.Monetary(groups=_MARGIN_GROUPS)
    margin_percent = fields.Float(groups=_MARGIN_GROUPS)


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    purchase_price = fields.Float(groups=_MARGIN_GROUPS)
    margin = fields.Float(groups=_MARGIN_GROUPS)
    margin_percent = fields.Float(groups=_MARGIN_GROUPS)


class PosOrder(models.Model):
    _inherit = "pos.order"

    margin = fields.Float(groups=_MARGIN_GROUPS)
    margin_percent = fields.Float(groups=_MARGIN_GROUPS)


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    purchase_price = fields.Float(groups=_MARGIN_GROUPS)
    margin = fields.Float(groups=_MARGIN_GROUPS)
    margin_percent = fields.Float(groups=_MARGIN_GROUPS)

