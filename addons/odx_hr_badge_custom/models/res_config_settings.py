from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    badge_instagram_qr = fields.Image(
        related="company_id.badge_instagram_qr",
        readonly=False,
        string="Badge Instagram QR Code",
    )
    badge_instagram_qr_name = fields.Char(
        related="company_id.badge_instagram_qr_name",
        readonly=False,
        string="Badge Instagram QR Code Filename",
    )
