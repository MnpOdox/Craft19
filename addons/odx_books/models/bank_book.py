from dateutil.relativedelta import relativedelta
from odoo import api, fields, models, _, tools
from odoo.exceptions import AccessError, ValidationError, UserError
from datetime import datetime,date

class BankBook(models.Model):

    _name = 'bank.book'
    _description = 'Bank Book Records'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'
    _order = 'start_date desc, id desc'

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
    def _check_manager_approval_rights(self):
        if not self.env.user.has_group('odx_books.group_book_manager'):
            raise AccessError('Only Book Manager can mark as Done.')

    def action_confirm(self):
        self.write({'state': 'confirm'})

    def action_done(self):
        self._check_manager_approval_rights()
        self.sudo().write({'state': 'done'})

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
            if rec.state != 'confirm':
                continue
            existing_confirm_count = self.search_count([
                ('state', '=', 'confirm'),
                ('company_id', '=', rec.company_id.id),
                ('id', '!=', rec.id)
            ])
            if existing_confirm_count >= 2:
                raise ValidationError("Only 2 records can be in 'In Progress' state for this Company.")

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
    expense_id = fields.Many2one('expense.book', string='Expense Entry', readonly=True, copy=False, ondelete='set null')

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

    def _prepare_expense_vals(self):
        self.ensure_one()
        return {
            'date': self.date,
            'description': self.description,
            'head_id': self.head_id.id,
            'amount': self.amount,
            'company_id': self.company_id.id,
        }

    def _sync_expense_entry(self):
        expense_model = self.env['expense.book'].sudo()
        for rec in self:
            if rec.head_id and rec.head_id.auto_expense:
                vals = rec._prepare_expense_vals()
                if rec.expense_id:
                    rec.expense_id.sudo().write(vals)
                else:
                    rec.expense_id = expense_model.create(vals)
            elif rec.expense_id:
                rec.expense_id.sudo().unlink()
                rec.expense_id = False

    @api.model_create_multi
    def create(self, vals_list):
        records = super(BankbookLines, self).create(vals_list)
        records._sync_expense_entry()
        return records

    def write(self, vals):
        res = super(BankbookLines, self).write(vals)
        if any(k in vals for k in ['head_id', 'date', 'description', 'amount', 'company_id']):
            self._sync_expense_entry()
        return res

    def unlink(self):
        linked_expenses = self.mapped('expense_id')
        res = super(BankbookLines, self).unlink()
        if linked_expenses:
            linked_expenses.sudo().unlink()
        return res

