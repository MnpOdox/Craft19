# -*- coding: utf-8 -*-
#############################################################################
#
#
#
#    You can modify it under the terms of the GNU AFFERO
#    GENERAL PUBLIC LICENSE (AGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU AFFERO GENERAL PUBLIC LICENSE (AGPL v3) for more details.
#
#    You should have received a copy of the GNU AFFERO GENERAL PUBLIC LICENSE
#    (AGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
#############################################################################

{
    'name': 'Craft Pos Customisation',
    'version': '19.0.1.0.0',
    'category': 'POS',
    'summary': "POS",
    'author': "OdoxSofthub",
    'website': "http://www.odoxsofthub.com",
    'description': """ """,
    'depends': ['base','sale_stock', 'stock', 'delivery', 'account', 'sale_margin','point_of_sale','send_sms',
                ],
    'data': [
        'security/ir.model.access.csv',
        'views/res_partner_view.xml',
        'views/res_users_view.xml',
        'views/pos_views.xml',
        'views/ship.xml',
        'wizard/tracking_update.xml',
        'wizard/pos_payment_method_change.xml',
    ],
    'demo': [
    ],

    'assets': {
        'point_of_sale._assets_pos': [
            'odx_craft_pos_customisation/static/src/js/partner_online_order.js',
            'odx_craft_pos_customisation/static/src/xml/pos_extension.xml',
        ],
    },


    'images': ['static/description/icon.png'],
    'license': 'AGPL-3',
    'application': True,
    'installable': True,
    'auto_install': False,



}
