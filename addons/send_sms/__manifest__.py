{
    'name': "Send SMS",
    'version': '19.0.1.0.0',
    'author': "Debasish Dash",
    'category': 'Tools',
    'summary':'You can use multiple gateway for multiple sms template to send SMS.',
    'description':'Allows you to send SMS to the mobile no.',
    'website': "http://www.debweb.com",
    'depends': ['base','web','sale'],
    'data': [
        'security/ir.model.access.csv',
    ],
    'images':['static/description/banner.png'],
    # 'license': 'LGPL-3',
    'installable':True,
    'auto_install':False,
}
