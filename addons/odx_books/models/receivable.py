from odoo import api, fields, models, _, tools
from datetime import datetime,date




class Receivable(models.Model):
    _name = 'receivable.book'
    _description = ' Receivable Book'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'partner_id'



    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirm', 'Confirm'),
        ('close', 'Closed'),
    ], string='Status', default='draft', tracking=True)

    partner_id = fields.Many2one('res.partner',string="Partner Ledger", required=True)
    receivable_line_ids = fields.One2many('receivable.book.line','receivable_id')
    current_date = fields.Date(string="Date", readonly= True)
    balance = fields.Float(string="Balance",compute="_compute_amount")
    company_id = fields.Many2one('res.company',string='Company',default=lambda self: self.env.company,tracking=True)



    def action_confirm(self):

        self.state = 'confirm'

    def action_done(self):

        self.state = 'close'

    def action_draft (self):

        self.state = 'draft'

    @api.model_create_multi
    def create(self, vals_list):
        res = super(Receivable, self).create(vals_list)
        for record in res:
            record.current_date = date.today()
        return res

    @api.onchange('amount')
    def _compute_amount(self):
        for record in self:
                record.balance = sum(record.receivable_line_ids.mapped('amount'))


    # def unlink(self):
    #
    #     if self.state in ('confirm','close'):
    #
    #         raise Warning(
    #            ('You cannot delete this record because the record already created')
    #         )
    #     return super(Receivable, self).unlink()



class ReceivableLines(models.Model):
    _name = 'receivable.book.line'
    _description = 'Receivable Book Line'

    date = fields.Date(string='Date', default=date.today(), required=True)
    description = fields.Char(string='Description')
    amount = fields.Float(string='Amount')
    receivable_id = fields.Many2one('receivable.book')
    company_id = fields.Many2one('res.company', string='Company',default=lambda self: self.env.company)
