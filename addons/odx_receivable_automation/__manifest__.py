{
    "name": "Receivable Purchase and Payment Automation",
    "version": "19.0.1.0.0",
    "summary": "Synchronize purchases and Cash/Bank payments with Receivable Books",
    "author": "Odox SoftHub",
    "license": "LGPL-3",
    "depends": ["purchase", "odx_books"],
    "data": [
        "views/book_head_views.xml",
        "views/cash_bank_book_views.xml",
        "views/purchase_order_views.xml",
        "views/receivable_views.xml",
    ],
    "installable": True,
    "application": False,
}
