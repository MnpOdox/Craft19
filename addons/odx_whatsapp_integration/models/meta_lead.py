import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)


class MetaForm(models.Model):
    _inherit = "odx.meta.form"

    whatsapp_auto_send_enabled = fields.Boolean(
        string="Send WhatsApp Template Automatically",
        groups="odx_whatsapp_integration.group_whatsapp_manager",
    )
    whatsapp_auto_account_id = fields.Many2one(
        "odx.whatsapp.account",
        string="WhatsApp Business Number",
        groups="odx_whatsapp_integration.group_whatsapp_manager",
        domain="[('company_id', '=', company_id), ('active', '=', True)]",
    )
    whatsapp_auto_template_id = fields.Many2one(
        "odx.whatsapp.template",
        string="Automatic Template",
        groups="odx_whatsapp_integration.group_whatsapp_manager",
        domain="[('account_id', '=', whatsapp_auto_account_id), ('status', '=', 'approved'), ('active', '=', True)]",
    )
    whatsapp_auto_template_parameters = fields.Text(
        string="Template Variable Values",
        groups="odx_whatsapp_integration.group_whatsapp_manager",
        help=(
            "One body-variable value per line. Supported replacements: {{contact_name}}, "
            "{{lead_name}}, {{phone}}, {{form_name}}, and {{salesperson}}."
        ),
    )
    whatsapp_auto_last_sent_at = fields.Datetime(
        string="Last Automatic Send", readonly=True, copy=False,
        groups="odx_whatsapp_integration.group_whatsapp_manager",
    )
    whatsapp_auto_last_error = fields.Text(
        string="Last Automatic Send Error", readonly=True, copy=False,
        groups="odx_whatsapp_integration.group_whatsapp_manager",
    )

    @api.onchange("whatsapp_auto_account_id")
    def _onchange_whatsapp_auto_account_id(self):
        if self.whatsapp_auto_template_id.account_id != self.whatsapp_auto_account_id:
            self.whatsapp_auto_template_id = False

    def _configured_parameter_values(self):
        self.ensure_one()
        return [value.strip() for value in (self.whatsapp_auto_template_parameters or "").splitlines() if value.strip()]

    def _expected_parameter_count(self):
        self.ensure_one()
        template = self.whatsapp_auto_template_id
        return template._panel_data()[0]["parameter_count"] if template else 0

    @api.constrains(
        "whatsapp_auto_send_enabled", "whatsapp_auto_account_id",
        "whatsapp_auto_template_id", "whatsapp_auto_template_parameters",
    )
    def _check_whatsapp_auto_template(self):
        for form in self.filtered("whatsapp_auto_send_enabled"):
            account = form.whatsapp_auto_account_id
            template = form.whatsapp_auto_template_id
            if not account or not template:
                raise ValidationError(_("Select a WhatsApp business number and an approved template."))
            if account.company_id != form.company_id or template.account_id != account:
                raise ValidationError(_("The automatic WhatsApp account and template must belong to this form's company."))
            if not account.active or not template.active or template.status != "approved":
                raise ValidationError(_("The automatic WhatsApp account and template must be active and approved."))
            expected = form._expected_parameter_count()
            actual = len(form._configured_parameter_values())
            if actual != expected:
                raise ValidationError(_(
                    "Template %(template)s requires %(expected)s body-variable value(s); %(actual)s were configured.",
                    template=template.display_name, expected=expected, actual=actual,
                ))

    def _render_auto_parameters(self, lead):
        self.ensure_one()
        replacements = {
            "contact_name": lead.contact_name or lead.partner_name or lead.name or "",
            "lead_name": lead.name or "",
            "phone": lead.phone or lead.mobile or "",
            "form_name": self.name or "",
            "salesperson": lead.user_id.name or "",
        }
        values = self._configured_parameter_values()
        for index, value in enumerate(values):
            for key, replacement in replacements.items():
                value = value.replace("{{%s}}" % key, replacement)
            values[index] = value
        return values

    def _send_automatic_whatsapp_template(self, lead):
        self.ensure_one()
        lead = lead.sudo()
        account = self.whatsapp_auto_account_id.sudo()
        template = self.whatsapp_auto_template_id.sudo()
        lead.write({"whatsapp_auto_template_state": "pending", "whatsapp_auto_template_error": False})
        try:
            if not account.active or not template.active or template.status != "approved":
                raise ValidationError(_("The configured WhatsApp account or template is no longer available."))
            parameters = self._render_auto_parameters(lead)
            if len(parameters) != self._expected_parameter_count():
                raise ValidationError(_("The configured template-variable values no longer match the template."))
            conversation = self.env["odx.whatsapp.conversation"].sudo()._find_or_create_outbound(account, lead)
            message = conversation.sudo().send_template(template, parameters)
            lead.write({
                "whatsapp_auto_template_state": "sent",
                "whatsapp_auto_template_message_id": message.id,
                "whatsapp_auto_template_error": False,
            })
            self.sudo().write({"whatsapp_auto_last_sent_at": fields.Datetime.now(), "whatsapp_auto_last_error": False})
            lead.message_post(body=_(
                "WhatsApp template %(template)s was sent automatically for Meta form %(form)s.",
                template=template.display_name, form=self.display_name,
            ))
        except Exception as exc:  # lead creation must never be rolled back by messaging
            error = str(exc)[:2000]
            lead.write({"whatsapp_auto_template_state": "failed", "whatsapp_auto_template_error": error})
            self.sudo().write({"whatsapp_auto_last_error": error})
            lead.message_post(body=_(
                "Automatic WhatsApp template could not be sent: %s", error,
            ))
            _logger.exception("Automatic WhatsApp template failed for Meta lead %s", lead.id)

    def _import_payload(self, payload, event=None):
        self.ensure_one()
        lead_ref = str(payload.get("id") or "")
        existed = bool(lead_ref and self.env["crm.lead"].sudo().search_count([
            ("meta_lead_id", "=", lead_ref),
        ]))
        lead = super()._import_payload(payload, event=event)
        if not existed and self.whatsapp_auto_send_enabled:
            self._send_automatic_whatsapp_template(lead)
        return lead
