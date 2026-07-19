{
    "name": "POS Cash and Bank Book Automation",
    "version": "19.0.1.0.0",
    "summary": "Post POS session payments and cash movements to Cash/Bank Books",
    "author": "Odox SoftHub",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "odx_books"],
    "data": [
        "views/cash_bank_book_views.xml",
        "views/pos_payment_method_views.xml",
        "views/pos_session_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "odx_pos_books/static/src/js/cash_move_popup.js",
            "odx_pos_books/static/src/xml/cash_move_popup.xml",
        ],
    },
    "installable": True,
    "application": False,
}
