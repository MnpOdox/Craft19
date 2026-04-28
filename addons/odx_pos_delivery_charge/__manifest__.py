{
    'name': 'Pos DeliveryCharge & Discount',
    'version': '19.0.1.0.0',
    'author': 'Odox SoftHub',
    'website': 'http://www.odoxsofthub.com',
    'summary': 'odoo14 Development',
    'depends': ['mail', 'base', 'point_of_sale','pos_discount','stock'],
    'data': [
        'security/pos_security.xml',
        'views/pos_config.xml',
        'views/pos_session.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'odx_pos_delivery_charge/static/src/js/control_buttons.js',
            'odx_pos_delivery_charge/static/src/js/product_card_price.js',
            'odx_pos_delivery_charge/static/src/xml/control_buttons.xml',
            'odx_pos_delivery_charge/static/src/xml/product_card_price.xml',
        ],
    },
    "installable": True,
    "license": "LGPL-3",

}
