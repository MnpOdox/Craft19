{
    "name": "Payment Tracker",
    "version": "19.0.1.0.0",
    "summary": "Schedule, fund and record payments through Cash and Bank Books",
    "author": "Odox SoftHub",
    "license": "LGPL-3",
    "category": "Accounting",
    "depends": ["mail", "odx_receivable_automation"],
    "data": [
        "security/payment_tracker_security.xml",
        "security/ir.model.access.csv",
        "data/payment_tracker_sequence.xml",
        "data/payment_tracker_cron.xml",
        "views/payment_tracker_views.xml",
        "wizard/payment_tracker_payment_wizard_views.xml",
        "views/payment_tracker_menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "odx_payment_tracker/static/src/js/payment_tracker_dashboard.js",
            "odx_payment_tracker/static/src/xml/payment_tracker_dashboard.xml",
            "odx_payment_tracker/static/src/scss/payment_tracker_dashboard.scss",
        ],
    },
    "application": True,
    "installable": True,
}
