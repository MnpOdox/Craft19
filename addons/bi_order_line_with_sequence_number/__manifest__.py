# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.
{
    'name': "Order line Sequence Number",
    'version': '19.0.1.0.0',
    'category': 'Extra Tools',
    'summary': "Sales order line sequence number in order line with sequence number report purchase order line sequence sale order line sequence   purchase agreement order line report sales stock order line sequence sale order line number order line sequence number report",
    'description': """
    
        Order line Sequence Number Odoo App helps users to show the order line number sequential. User can show sequence number in sale quotation/order line, RFQ/purchase order line, incoming/outgoing/detailed/all operation line, invoice/bill/credit note/refund line, purchase agreement line and manufacturing order line with its reports.

    """,
    'author': 'BrowseInfo',
    'website': 'https://www.browseinfo.com',
    'depends': ['base','sale_management','purchase','account','stock','purchase_requisition'],
    'data': [
        'security/access_display_order_line.xml'

    ],
    'license':'OPL-1',
    'installable': True,
    'auto_install': False,
    'live_test_url':'https://youtu.be/GMRr7UUoDn0',
    'images':['static/description/Order-line-with-sequence-number-Banner.gif'],
}
