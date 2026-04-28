from odoo import fields, models



class CashBookHead(models.Model):

    _name = 'book.head'
    _description = 'Book Head'
    _rec_name = 'head_name'

    head_name = fields.Char(string="Head Name", required=True)
    cash = fields.Boolean(string="Cash Book",default=True)
    bank = fields.Boolean(string="Bank Book", default=True)
    expense = fields.Boolean(string="Expense",default=True)
    auto_expense = fields.Boolean(
        string="Auto Create Expense Line",
        default=False,
        help="If enabled, creating a Cash/Bank line with this head automatically creates an Expense Book entry.",
    )
