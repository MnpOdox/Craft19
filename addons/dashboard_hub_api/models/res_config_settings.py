from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    dashboard_hub_api_enabled = fields.Boolean(
        string="Enable Dashboard Hub API",
        config_parameter="dashboard_hub_api.enabled",
        default=False,
    )
    dashboard_hub_api_key = fields.Char(
        string="Dashboard Hub API Key",
        config_parameter="dashboard_hub_api.api_key",
        default="change-me",
    )
    dashboard_hub_api_key_header = fields.Char(
        string="Dashboard Hub API Key Header",
        config_parameter="dashboard_hub_api.api_key_header",
        default="X-DASHBOARD-KEY",
    )
    dashboard_hub_api_source_name = fields.Char(
        string="Dashboard Source Name",
        config_parameter="dashboard_hub_api.source_name",
        default="Main Odoo Server",
        help="Displayed in drill-down metadata so the central dashboard knows which source served the data.",
    )
