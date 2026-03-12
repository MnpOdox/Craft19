from dateutil.relativedelta import relativedelta
from odoo import api, fields, models, _, tools
from odoo.exceptions import ValidationError
from datetime import datetime,date



class BookExpense(models.Model):

    _name = 'expense.book'
    _description = ' Expense Book'


    date = fields.Date(string="Date", required=True, default=datetime.today())
    description = fields.Char(string="Description")
    head_id = fields.Many2one('book.head', required=True, domain=[('expense','=',True)])
    amount = fields.Float( string='Amount')
    company_id = fields.Many2one('res.company',string='Company',default=lambda self: self.env.company)




