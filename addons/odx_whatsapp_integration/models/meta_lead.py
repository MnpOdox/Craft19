from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class MetaWhatsAppFollowupStep(models.Model):
    _name = "odx.meta.whatsapp.followup.step"
    _description = "Meta Lead WhatsApp Follow-Up Step"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    form_id = fields.Many2one("odx.meta.form", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="form_id.company_id", store=True, index=True)
    account_id = fields.Many2one(related="form_id.whatsapp_auto_account_id", store=True)
    template_id = fields.Many2one(
        "odx.whatsapp.template", string="Approved Template", required=True,
        domain="[('account_id', '=', account_id), ('status', '=', 'approved'), ('active', '=', True)]",
    )
    delay_hours = fields.Float(
        string="Wait After Previous Message (Hours)", required=True, default=0.0,
        help="The first step must be zero. Later steps wait this many hours after the prior successful send.",
    )
    template_parameters = fields.Text(
        string="Template Variable Values",
        help=(
            "One body-variable value per line. Supported replacements: {{contact_name}}, "
            "{{lead_name}}, {{phone}}, {{form_name}}, and {{salesperson}}."
        ),
    )

    def _configured_parameter_values(self):
        self.ensure_one()
        return [value.strip() for value in (self.template_parameters or "").splitlines() if value.strip()]

    def _expected_parameter_count(self):
        self.ensure_one()
        return self.template_id._panel_data()[0]["parameter_count"] if self.template_id else 0

    def _render_parameters(self, lead):
        self.ensure_one()
        replacements = {
            "contact_name": lead.contact_name or lead.partner_name or lead.name or "",
            "lead_name": lead.name or "",
            "phone": lead.phone or lead.mobile or "",
            "form_name": self.form_id.name or "",
            "salesperson": lead.user_id.name or "",
        }
        values = self._configured_parameter_values()
        for index, value in enumerate(values):
            for key, replacement in replacements.items():
                value = value.replace("{{%s}}" % key, replacement)
            values[index] = value
        return values

    @api.constrains("form_id", "template_id", "delay_hours", "template_parameters")
    def _check_configuration(self):
        for step in self:
            if step.delay_hours < 0:
                raise ValidationError(_("A follow-up delay cannot be negative."))
            if not step.form_id.whatsapp_auto_account_id:
                raise ValidationError(_("Select the WhatsApp business number before adding follow-up steps."))
            if step.template_id.account_id != step.form_id.whatsapp_auto_account_id:
                raise ValidationError(_("Every follow-up template must belong to the selected WhatsApp account."))
            if not step.template_id.active or step.template_id.status != "approved":
                raise ValidationError(_("Every follow-up template must be active and approved by Meta."))
            expected = step._expected_parameter_count()
            actual = len(step._configured_parameter_values())
            if actual != expected:
                raise ValidationError(_(
                    "Template %(template)s requires %(expected)s body-variable value(s); %(actual)s were configured.",
                    template=step.template_id.display_name, expected=expected, actual=actual,
                ))

    @api.model_create_multi
    def create(self, vals_list):
        steps = super().create(vals_list)
        steps.mapped("form_id")._validate_whatsapp_followup_configuration()
        return steps

    def write(self, vals):
        forms = self.mapped("form_id")
        result = super().write(vals)
        (forms | self.mapped("form_id"))._validate_whatsapp_followup_configuration()
        return result

    def unlink(self):
        forms = self.mapped("form_id")
        result = super().unlink()
        forms._validate_whatsapp_followup_configuration()
        return result


class MetaForm(models.Model):
    _inherit = "odx.meta.form"

    whatsapp_auto_send_enabled = fields.Boolean(
        string="Enable WhatsApp Follow-Up Automation",
        groups="odx_whatsapp_integration.group_whatsapp_manager",
    )
    whatsapp_auto_account_id = fields.Many2one(
        "odx.whatsapp.account", string="WhatsApp Business Number",
        groups="odx_whatsapp_integration.group_whatsapp_manager",
        domain="[('company_id', '=', company_id), ('active', '=', True)]",
    )
    whatsapp_followup_step_ids = fields.One2many(
        "odx.meta.whatsapp.followup.step", "form_id", string="Follow-Up Messages",
        groups="odx_whatsapp_integration.group_whatsapp_manager",
    )
    whatsapp_auto_close_hours = fields.Float(
        string="Close After Final Message (Hours)", default=48.0,
        groups="odx_whatsapp_integration.group_whatsapp_manager",
    )
    # Retained for upgrade compatibility. Existing values are converted into
    # the first sequence step by the post-migration script.
    whatsapp_auto_template_id = fields.Many2one(
        "odx.whatsapp.template", string="Legacy Automatic Template",
        groups="odx_whatsapp_integration.group_whatsapp_manager",
        domain="[('account_id', '=', whatsapp_auto_account_id), ('status', '=', 'approved'), ('active', '=', True)]",
    )
    whatsapp_auto_template_parameters = fields.Text(
        string="Legacy Template Variable Values",
        groups="odx_whatsapp_integration.group_whatsapp_manager",
    )
    whatsapp_auto_last_sent_at = fields.Datetime(
        string="Last Automatic Send", readonly=True, copy=False,
        groups="odx_whatsapp_integration.group_whatsapp_manager",
    )
    whatsapp_auto_last_error = fields.Text(
        string="Last Automation Error", readonly=True, copy=False,
        groups="odx_whatsapp_integration.group_whatsapp_manager",
    )

    @api.onchange("whatsapp_auto_account_id")
    def _onchange_whatsapp_auto_account_id(self):
        if self.whatsapp_auto_template_id.account_id != self.whatsapp_auto_account_id:
            self.whatsapp_auto_template_id = False
        for step in self.whatsapp_followup_step_ids:
            if step.template_id.account_id != self.whatsapp_auto_account_id:
                step.template_id = False

    @api.constrains(
        "whatsapp_auto_send_enabled", "whatsapp_auto_account_id",
        "whatsapp_followup_step_ids", "whatsapp_auto_close_hours",
    )
    def _check_whatsapp_followup_automation(self):
        self._validate_whatsapp_followup_configuration()

    def _validate_whatsapp_followup_configuration(self):
        for form in self.filtered("whatsapp_auto_send_enabled"):
            if not form.whatsapp_auto_account_id:
                raise ValidationError(_("Select a WhatsApp business number for the follow-up automation."))
            steps = form.whatsapp_followup_step_ids.sorted(key=lambda item: (item.sequence, item.id))
            if not steps:
                raise ValidationError(_("Add at least one approved WhatsApp template to the follow-up automation."))
            steps._check_configuration()
            if steps[0].delay_hours != 0:
                raise ValidationError(_("The first WhatsApp follow-up step must have a zero-hour delay."))
            if any(step.delay_hours <= 0 for step in steps[1:]):
                raise ValidationError(_("Every WhatsApp follow-up after the first must have a delay greater than zero."))
            if form.whatsapp_auto_close_hours <= 0:
                raise ValidationError(_("The automatic close delay must be greater than zero hours."))

    def _start_whatsapp_followup_automation(self, lead):
        self.ensure_one()
        steps = self.whatsapp_followup_step_ids.sorted(key=lambda item: (item.sequence, item.id))
        if not self.whatsapp_auto_send_enabled or not steps:
            return False
        lead = lead.sudo()
        now = fields.Datetime.now()
        lead.with_context(odx_whatsapp_automation_write=True).write({
            "whatsapp_automation_state": "running",
            "whatsapp_automation_started_at": now,
            "whatsapp_automation_next_step_id": steps[0].id,
            "whatsapp_automation_next_run_at": now,
            "whatsapp_automation_last_sent_at": False,
            "whatsapp_automation_last_message_id": False,
            "whatsapp_automation_retry_count": 0,
            "whatsapp_automation_error": False,
            "whatsapp_automation_completion_reason": False,
            "whatsapp_automation_auto_closed": False,
            "whatsapp_auto_template_state": "pending",
            "whatsapp_auto_template_error": False,
        })
        lead._process_whatsapp_followup_automation()
        return True

    def _send_automatic_whatsapp_template(self, lead):
        """Compatibility entry point used by existing integrations."""
        self.ensure_one()
        return self._start_whatsapp_followup_automation(lead)

    def _import_payload(self, payload, event=None):
        self.ensure_one()
        lead_ref = str(payload.get("id") or "")
        existed = bool(lead_ref and self.env["crm.lead"].sudo().search_count([
            ("meta_lead_id", "=", lead_ref),
        ]))
        lead = super()._import_payload(payload, event=event)
        # Ad-level routing can deliberately leave an event waiting until its
        # route is configured.  In that case the Meta importer returns an
        # empty lead recordset and automation must not start yet.
        if lead and not existed and self.whatsapp_auto_send_enabled:
            self._start_whatsapp_followup_automation(lead)
        return lead
