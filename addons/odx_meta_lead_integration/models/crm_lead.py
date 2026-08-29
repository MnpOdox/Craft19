from odoo import fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    meta_lead_id = fields.Char(index=True, copy=False, readonly=True)
    meta_page_id = fields.Many2one("odx.meta.page", copy=False, readonly=True)
    meta_form_id = fields.Many2one("odx.meta.form", copy=False, readonly=True)
    meta_created_time = fields.Datetime(copy=False, readonly=True)
    meta_campaign_name = fields.Char(copy=False, readonly=True)
    meta_adset_name = fields.Char(copy=False, readonly=True)
    meta_ad_name = fields.Char(copy=False, readonly=True)

    _meta_lead_unique = models.Constraint(
        "UNIQUE(meta_lead_id)", "A Meta lead submission can only be imported once."
    )
