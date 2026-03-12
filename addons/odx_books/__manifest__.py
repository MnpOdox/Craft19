{
    'name': 'Cash Bank Books',
    'version': '19.0.1.0.0',
    'author': 'Odox SoftHub',
    'website': 'http://www.odoxsofthub.com',
    'summary': 'odoo14 Development',
    'depends': ['base', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/menu.xml',
        'views/cash_book.xml',
        'views/bank_book.xml',
        'views/expense.xml',
        'views/cash_book_head.xml',
        'views/receivable.xml'
    ],
    "installable": True,

}
