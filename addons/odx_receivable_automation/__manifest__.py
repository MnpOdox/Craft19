{
    "name": "Receivable Purchase and Payment Automation",
    "version": "19.0.2.0.0",
    "summary": "Synchronize purchases, salary, payments, and POS credit sales with Receivable Books",
    "author": "Odox SoftHub",
    "license": "LGPL-3",
    "depends": ["purchase", "odx_pos_books"],
    "data": [
        "views/book_head_views.xml",
        "views/cash_bank_book_views.xml",
        "views/expense_views.xml",
        "views/pos_credit_views.xml",
        "views/purchase_order_views.xml",
        "views/receivable_views.xml",
    ],
    "installable": True,
    "application": False,
}
