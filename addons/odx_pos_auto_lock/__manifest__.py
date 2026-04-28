{
    "name": "Odx POS Auto Lock",
    "version": "19.0.1.0.0",
    "category": "Point of Sale",
    "summary": "Automatically lock the POS screen after cashier inactivity.",
    "author": "Odox SoftHub",
    "depends": ["point_of_sale", "pos_hr"],
    "data": [
        "views/pos_config_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "odx_pos_auto_lock/static/src/js/pos_auto_lock.js",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
