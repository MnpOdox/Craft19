from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    badge_instagram_qr = fields.Image(
        string="Instagram QR Code",
        max_width=512,
        max_height=512,
        help="QR code image shown on employee badges.",
    )
    badge_instagram_qr_name = fields.Char(string="Instagram QR Code Filename")
