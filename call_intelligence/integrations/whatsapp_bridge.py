import re

import frappe

PHONE_FIELDS = ("mobile_no", "phone", "whatsapp_no", "phone_no")
REFERENCE_DOCTYPES = ("CRM Lead", "Lead", "Contact")


def _normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\D", "", value)


MEDPLUM_BUTTON_LABELS = {
    "medplum_confirm": "Confirm",
    "medplum_cancel": "Cancel",
    "medplum_reschedule": "Reschedule",
}


def _reference_from_reply_chain(doc, max_hops: int = 6):
    """Link incoming replies to the same lead as the message being replied to (needed when many leads share one WhatsApp number)."""
    if doc.get("type") != "Incoming":
        return None, None
    rid = doc.get("reply_to_message_id")
    hops = 0
    while rid and hops < max_hops:
        row = frappe.db.get_value(
            "WhatsApp Message",
            {"message_id": rid},
            ["reference_doctype", "reference_name", "reply_to_message_id"],
            as_dict=True,
        )
        if not row:
            break
        if row.get("reference_doctype") and row.get("reference_name"):
            return row["reference_doctype"], row["reference_name"]
        rid = row.get("reply_to_message_id")
        hops += 1
    return None, None


def _communication_content(doc) -> str:
    if doc.get("content_type") == "button" and doc.get("type") == "Incoming":
        key = (doc.get("message") or "").strip().lower()
        if key in MEDPLUM_BUTTON_LABELS:
            return f"Patient chose: {MEDPLUM_BUTTON_LABELS[key]}"
    return doc.get("message") or ""


def _find_reference_by_phone(phone: str):
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        return None, None

    for doctype in REFERENCE_DOCTYPES:
        if not frappe.db.exists("DocType", doctype):
            continue

        meta = frappe.get_meta(doctype)
        available_phone_fields = [f for f in PHONE_FIELDS if meta.has_field(f)]
        if not available_phone_fields:
            continue

        for fieldname in available_phone_fields:
            direct = frappe.db.get_value(doctype, {fieldname: phone}, "name")
            if direct:
                return doctype, direct

        sample_fields = ["name", *available_phone_fields]
        candidates = frappe.get_all(doctype, fields=sample_fields, limit_page_length=500)
        for row in candidates:
            for fieldname in available_phone_fields:
                candidate_phone = _normalize_phone(row.get(fieldname))
                if not candidate_phone:
                    continue
                if candidate_phone.endswith(normalized_phone[-8:]) or normalized_phone.endswith(candidate_phone[-8:]):
                    return doctype, row.get("name")

    return None, None


def _attach_reference(whatsapp_message):
    if whatsapp_message.reference_doctype and whatsapp_message.reference_name:
        return whatsapp_message.reference_doctype, whatsapp_message.reference_name

    if whatsapp_message.get("type") == "Incoming":
        thread_dt, thread_dn = _reference_from_reply_chain(whatsapp_message)
        if thread_dt and thread_dn:
            frappe.db.set_value(
                "WhatsApp Message",
                whatsapp_message.name,
                {
                    "reference_doctype": thread_dt,
                    "reference_name": thread_dn,
                },
                update_modified=False,
            )
            whatsapp_message.reference_doctype = thread_dt
            whatsapp_message.reference_name = thread_dn
            return thread_dt, thread_dn

    phone = whatsapp_message.get("from") if whatsapp_message.get("type") == "Incoming" else whatsapp_message.get("to")
    reference_doctype, reference_name = _find_reference_by_phone(phone)

    if reference_doctype and reference_name:
        frappe.db.set_value(
            "WhatsApp Message",
            whatsapp_message.name,
            {
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
            },
            update_modified=False,
        )
        whatsapp_message.reference_doctype = reference_doctype
        whatsapp_message.reference_name = reference_name

    return reference_doctype, reference_name


def _safe_email(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    return value if "@" in value else ""


def sync_whatsapp_to_communication(doc, method=None):
    """Mirror WhatsApp Message entries into Communication records."""
    try:
        if doc.doctype != "WhatsApp Message":
            return

        reference_doctype, reference_name = _attach_reference(doc)
        if not reference_doctype or not reference_name:
            return

        message_id = doc.get("message_id")
        if message_id and frappe.db.exists(
            "Communication",
            {
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "message_id": message_id,
            },
        ):
            return

        direction = "Received" if doc.get("type") == "Incoming" else "Sent"
        from_value = doc.get("from") or ""
        to_value = doc.get("to") or ""

        communication_doc = frappe.get_doc(
            {
                "doctype": "Communication",
                "subject": f"WhatsApp {direction}: {reference_name}",
                "communication_type": "Communication",
                "communication_medium": "Chat",
                "status": "Linked",
                "sent_or_received": direction,
                "sender": _safe_email(from_value),
                "recipients": _safe_email(to_value),
                "phone_no": from_value if direction == "Received" else to_value,
                "content": _communication_content(doc),
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "message_id": message_id,
            }
        )
        communication_doc.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "call_intelligence.whatsapp_bridge")


def whatsapp_message_after_insert(doc, method=None):
    """Run Communication sync then Medplum interactive auto-replies."""
    sync_whatsapp_to_communication(doc, method)
    from call_intelligence.integrations.medplum_followup import handle_medplum_flow_reply

    handle_medplum_flow_reply(doc, method)
