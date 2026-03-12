{
    'name': 'POS Custom',
    'version': '19.0.1.0.0',
    'category': 'HR',
    'depends': ['base', 'point_of_sale', 'pos_restaurant'],
    'author': 'Odox SoftHub',
'description': """Remove Taxe from Receipt""",
    'website': 'http://www.odoxsofthub.com',
    'license': 'GPL-3',
    'data': [
            'report/pos_rep.xml',
            'views/pos_order.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'odx_pos_receipt_custom/static/src/js/model.js',
            'odx_pos_receipt_custom/static/src/xml/ReceiptScreen.xml',
        ],
    },
    'installable': True,
    'application': True,
}
