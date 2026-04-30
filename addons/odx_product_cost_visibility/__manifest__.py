{
    'name': 'Product Cost Visibility',
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'author': 'Odox SoftHub',
    'description': """ Restrict access to product cost and margin for salespersons and make it visible only to sales managers. """,
    'website': 'http://www.odoxsofthub.com',
    'license': 'GPL-3',
    'depends': ['product','pos_margin','sales_team','stock_account'],
    'data': [
        'views/product_category.xml',
    ],
    'installable': True,
    'application': True,
    'qweb': [ ],
}
