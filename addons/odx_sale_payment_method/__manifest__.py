{
    'name': 'Sale Payment Method',
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'summary': 'sale Payment method',
    'description': """ Sale Payment method """,
    'depends': ['sale'],
    'author': 'Odox SoftHub',
    'website': 'https://odoxsofthub.com',
    'license': 'LGPL-3',
    'data': [
        'security/ir.model.access.csv',
        'views/sale_payment_method_view.xml',
        'views/sale_order_view.xml',
    ],
}
