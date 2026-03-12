from dateutil.relativedelta import relativedelta
from odoo import api, fields, models, _, tools
from odoo.exceptions import ValidationError,UserError
from datetime import datetime,date

class BankBook(models.Model):

    _name = 'bank.book'
    _description = 'Bank Book Records'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    name = fields.Char(string='Name', required=True)
    start_date = fields.Date(string='Start Date', required=True, default=lambda self: self._get_first_day_of_current_month())
    end_date = fields.Date(string='End Date',required=True, default=lambda self: self._get_last_day_of_current_month())
    open_balance = fields.Float(string='Opening Balance',tracking=True)
    cur_balance = fields.Float(string='Current Balance',compute='_compute_cur_balance')
    bank_line_ids = fields.One2many('bank.book.line','name_id')
    get_bal = fields.Float('Get Balance',store=True)
    company_id = fields.Many2one('res.company',string='Company',default=lambda self: self.env.company,tracking=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirm', 'In Progress'),
        ('done', 'Done'),
        ('cancel', 'Cancelled')
    ], string='Status', default='draft', tracking=True)

    @api.model
    def _get_first_day_of_current_month(self):
        today = date.today()
        return today.replace(day=1)

    @api.model
    def _get_last_day_of_current_month(self):
        today = date.today()
        first_day_of_next_month = today + relativedelta(months=1, day=1)
        last_day_of_current_month = first_day_of_next_month - relativedelta(days=1)
        return last_day_of_current_month

    # button conditions
    def action_confirm(self):
        self.state = 'confirm'

    def action_done(self):
        self.state = 'done'

    def action_draft(self):
        self.state = 'draft'

    def action_cancel(self):
        self.state = 'cancel'

    def action_open_receivable(self):
        self.ensure_one()
        return self.env["ir.actions.act_window"]._for_xml_id("odx_books.action_receivable_book")

    def action_open_expense(self):
        self.ensure_one()
        return self.env["ir.actions.act_window"]._for_xml_id("odx_books.action_book_expense")

    # get the total amount + open balance = current balance

    @api.depends('open_balance', 'bank_line_ids.amount')
    def _compute_cur_balance(self):
        for rec in self:
            cur_balance = rec.open_balance + sum(rec.bank_line_ids.mapped('amount'))
            rec.cur_balance = cur_balance

    # action button  in delete button clicking time raise error
    def unlink(self):

        if self.state in ('done','confirm'):

            raise UserError(
               ('You cannot delete this record because the record already created')
            )
        return super(BankBook, self).unlink()

    @api.constrains('state', 'company_id')
    def _check_existing_confirm_record(self):
        for rec in self:
            existing_confirm_record = self.search([
                ('state', '=', 'confirm'),
                ('company_id', '=', rec.company_id.id),
                ('id', '!=', rec.id)
            ])
            if existing_confirm_record and rec.state == 'confirm':
                raise ValidationError("There is already a record in 'In Progress' state for this company.")

    # @api.constrains('state')
    # def _check_existing_confirm_record(self):
    #     existing_confirm_record = self.search([('state', '=', 'confirm'), ('id', '!=', self.id)])
    #     if existing_confirm_record and self.state == 'confirm':
    #         raise ValidationError("There is already a record in 'In Progress' state!!!")


class BankbookLines(models.Model):

    _name = 'bank.book.line'
    _description = 'Bank Book Line'



    date = fields.Date(string='Date', default=date.today() , required=True)
    head_id = fields.Many2one('book.head',required=True, domain=[('bank', '=', True)])
    description = fields.Char(string='Description')
    amount = fields.Float(string='Amount')
    balance = fields.Float(string="Balance",compute='_compute_balance_amount',store=False)
    name_id = fields.Many2one('bank.book')
    company_id = fields.Many2one('res.company', string='Company',default=lambda self: self.env.company)

    # compute the balance in bank_line_ids
    @api.depends('amount', 'name_id.open_balance')

    def _compute_balance_amount(self):
        for rec in self:
            balance = 0
            rec.balance = 0
            previous_amounts = 0
            for line in rec.name_id.bank_line_ids:
                # if not 'ref' in str(line):

                previous_amounts += line.amount
                if rec == line:
                    break
            rec.balance = rec.name_id.open_balance + previous_amounts


