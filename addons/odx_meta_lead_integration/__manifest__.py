{
    "name": "ODX Meta Lead Integration",
    "version": "19.0.3.1.0",
    "summary": "Import Meta Lead Ads into CRM with secure webhooks and assignment",
    "author": "OpenAI",
    "license": "LGPL-3",
    "depends": ["crm", "utm", "mail"],
    "data": [
        "security/meta_lead_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/meta_lead_views.xml",
        "views/crm_lead_views.xml",
        "views/menu.xml",
    ],
    "installable": True,
    "application": True,
}
