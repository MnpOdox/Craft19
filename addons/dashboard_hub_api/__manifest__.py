{
    "name": "Dashboard Hub API",
    "version": "19.0.1.0.0",
    "summary": "Reporting API for the standalone multi-region dashboard hub.",
    "author": "OpenAI",
    "license": "LGPL-3",
    "depends": [
        "base",
        "base_setup",
        "point_of_sale",
        "purchase",
        "stock",
        "odx_books",
    ],
    "data": [
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
}
