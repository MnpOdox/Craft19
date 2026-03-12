from odoo import api, fields, models, _, tools
from odoo.exceptions import ValidationError



class CashBookHead(models.Model):

    _name = 'book.head'
    _description = 'Book Head'
    _rec_name = 'head_name'

    head_name = fields.Char(string="Head Name", required=True)
    cash = fields.Boolean(string="Cash Book",default=True)
    bank = fields.Boolean(string="Bank Book", default=True)
    expense = fields.Boolean(string="Expense",default=True)
