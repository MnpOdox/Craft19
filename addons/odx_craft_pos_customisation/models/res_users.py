# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class ResUsers(models.Model):
    _inherit = 'res.users'


    login_types = fields.Selection([
        ('user', 'User'),
        ('employee', 'Employee'),
    ], string='Login Type',required=True,default='user'
    )



