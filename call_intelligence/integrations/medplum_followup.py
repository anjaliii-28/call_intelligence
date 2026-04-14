"""Auto-replies for Medplum follow-up WhatsApp interactive button taps."""

from __future__ import annotations

import frappe

from call_intelligence.integrations.whatsapp_bridge import (
    _find_reference_by_phone,
    _reference_from_reply_chain,
)

MEDPLUM_BUTTON_IDS = frozenset({"medplum_confirm", "medplum_cancel", "medplum_reschedule"})


def handle_medplum_flow_reply(doc, method=None):
    """Respond to Confirm / Cancel / Reschedule after Medplum encounter outreach."""
    try:
        if doc.doctype != "WhatsApp Message" or doc.type != "Incoming":
            return
        if (doc.content_type or "").lower() != "button":
            return

        button_id = (doc.message or "").strip().lower()
        if button_id not in MEDPLUM_BUTTON_IDS:
            return

        rdt, rname = doc.reference_doctype, doc.reference_name
        if not rdt or not rname:
            rdt, rname = _reference_from_reply_chain(doc)
        if not rdt or not rname:
            rdt, rname = _find_reference_by_phone(doc.get("from") or "")
        if not rdt or not rname:
            return

        if button_id == "medplum_confirm":
            text = "ThankYou for the Confirmation !"
        elif button_id == "medplum_cancel":
            text = "Thank you. We have recorded your choice to cancel. Our team will follow up if needed."
        else:
            text = (
                "Thank you. We have noted that you would like to reschedule. "
                "Our team will contact you shortly."
            )

        frappe.get_doc(
            {
                "doctype": "WhatsApp Message",
                "type": "Outgoing",
                "message": text,
                "content_type": "text",
                "reference_doctype": rdt,
                "reference_name": rname,
            }
        ).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "call_intelligence.medplum_followup")
