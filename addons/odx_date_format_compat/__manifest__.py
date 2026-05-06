{
    "name": "Date Format Compatibility (Numeric Lists)",
    "summary": "Render list-view dates using res.lang.date_format (e.g. 01/08/2024) instead of the locale-natural format (1 Aug 2024).",
    "version": "19.0.1.0.0",
    "category": "Tools",
    "author": "Odox SoftHub",
    "license": "LGPL-3",
    "depends": ["web"],
    "assets": {
        "web.assets_backend": [
            "odx_date_format_compat/static/src/js/date_format_compat.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
