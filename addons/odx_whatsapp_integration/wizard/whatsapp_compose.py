import json
import mimetypes

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class WhatsAppCompose(models.TransientModel):
    _name = "odx.whatsapp.compose"
    _description = "Compose WhatsApp Message"

    lead_id = fields.Many2one("crm.lead", required=True)
    account_id = fields.Many2one("odx.whatsapp.account", required=True, domain="[('company_id', '=', company_id), ('active', '=', True)]")
    company_id = fields.Many2one(related="lead_id.company_id")
    conversation_id = fields.Many2one("odx.whatsapp.conversation", readonly=True)
    mode = fields.Selection([("text", "Free-form text"), ("template", "Approved template"), ("media", "Media")], default="text", required=True)
    body = fields.Text()
    template_id = fields.Many2one("odx.whatsapp.template", domain="[('account_id', '=', account_id), ('status', '=', 'approved'), ('active', '=', True)]")
    template_parameters = fields.Char(help="JSON list of body values, for example: [\"John\", \"SO001\"]")
    media_type = fields.Selection([("image", "Image"), ("document", "Document"), ("audio", "Audio"), ("video", "Video")])
    attachment = fields.Binary(attachment=False)
    attachment_name = fields.Char()

    @api.onchange("lead_id")
    def _onchange_lead_id(self):
        if self.lead_id:
            self.account_id = self.env["odx.whatsapp.account"].search([
                ("company_id", "=", self.lead_id.company_id.id), ("active", "=", True)
            ], limit=1)

    @api.onchange("attachment_name")
    def _onchange_attachment_name(self):
        mimetype = mimetypes.guess_type(self.attachment_name or "")[0] or ""
        if mimetype.startswith("image/"):
            self.media_type = "image"
        elif mimetype.startswith("audio/"):
            self.media_type = "audio"
        elif mimetype.startswith("video/"):
            self.media_type = "video"
        elif mimetype:
            self.media_type = "document"

    def _conversation(self):
        self.ensure_one()
        return self.env["odx.whatsapp.conversation"]._find_or_create_outbound(self.account_id, self.lead_id)

    def action_send(self):
        self.ensure_one()
        conversation = self._conversation()
        if self.mode == "text":
            if not self.body:
                raise ValidationError(_("Enter a message."))
            conversation.send_text(self.body)
        elif self.mode == "template":
            try:
                parameters = json.loads(self.template_parameters or "[]")
            except ValueError as exc:
                raise ValidationError(_("Template parameters must be a valid JSON list.")) from exc
            if not isinstance(parameters, list) or not all(isinstance(item, str) for item in parameters):
                raise ValidationError(_("Template parameters must be a JSON list of text values."))
            conversation.send_template(self.template_id, parameters)
        else:
            if not self.attachment or not self.media_type:
                raise ValidationError(_("Select a media file and its type."))
            conversation.send_media(self.media_type, self.attachment, self.attachment_name, caption=self.body)
        return {"type": "ir.actions.act_window_close"}
