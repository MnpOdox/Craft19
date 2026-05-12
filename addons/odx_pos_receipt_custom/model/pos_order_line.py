import json

from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    receipt_note_text = fields.Char(
        string="Receipt Note",
        compute="_compute_receipt_note_text",
    )

    @api.depends("note")
    def _compute_receipt_note_text(self):
        for line in self:
            line.receipt_note_text = self._normalize_receipt_note(line.note)

    @classmethod
    def _normalize_receipt_note(cls, note):
        if not note:
            return ""
        if isinstance(note, list):
            return ", ".join(filter(None, (cls._normalize_receipt_note(item) for item in note)))
        if isinstance(note, dict):
            for key in ("text", "label", "name", "value", "note"):
                value = note.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return ", ".join(filter(None, (cls._normalize_receipt_note(value) for value in note.values())))
        if not isinstance(note, str):
            return str(note)
        try:
            parsed = json.loads(note)
        except Exception:
            return note
        return cls._normalize_receipt_note(parsed)
