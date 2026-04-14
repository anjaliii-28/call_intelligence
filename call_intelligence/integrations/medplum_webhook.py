"""Medplum FHIR Subscription (rest-hook) receiver for Encounter updates."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from typing import Any

import frappe
from frappe import _

MEDPLUM_SIGNATURE_HEADER = "HTTP_X_MEDPLUM_SIGNATURE"
MEDPLUM_DELETED_HEADER = "HTTP_X_MEDPLUM_DELETED_RESOURCE"

# IDs are stored on incoming taps (frappe_whatsapp uses button_reply.id as message body).
MEDPLUM_FOLLOWUP_BUTTONS: list[dict[str, str]] = [
    {"id": "medplum_confirm", "title": "Confirm"},
    {"id": "medplum_cancel", "title": "Cancel"},
    {"id": "medplum_reschedule", "title": "Reschedule"},
]


def _encounter_blurb_for_patient(encounter: dict[str, Any]) -> str:
    """Human-readable lines describing what this encounter was about (for WhatsApp + context)."""
    lines: list[str] = []
    st = encounter.get("status")
    if st:
        lines.append(f"Visit status: {st}")

    for t in encounter.get("type") or []:
        text = t.get("text")
        if not text and t.get("coding"):
            text = (t["coding"][0] or {}).get("display")
        if text:
            lines.append(f"Visit type: {str(text)[:200]}")
            break

    for rc in encounter.get("reasonCode") or []:
        txt = rc.get("text")
        if not txt and rc.get("coding"):
            txt = (rc["coding"][0] or {}).get("display")
        if txt:
            lines.append(f"Reason: {str(txt)[:200]}")
            break

    for d in encounter.get("diagnosis") or []:
        cond = d.get("condition") or {}
        disp = cond.get("display")
        if disp:
            lines.append(f"Diagnosis: {str(disp)[:200]}")
            break

    for ext in encounter.get("extension") or []:
        url = (ext.get("url") or "").lower()
        if any(k in url for k in ("follow", "instruction", "careplan", "procedure")):
            val = ext.get("valueString") or ext.get("valueMarkdown") or ext.get("valueCode")
            if val:
                lines.append(f"Follow-up: {str(val)[:200]}")
                break

    if not lines:
        return ""
    return "\n".join(lines[:5])[:900]


def _build_patient_intro(encounter: dict[str, Any]) -> str:
    """Opening text tied to this encounter; second block explains what the visit was about."""
    base = "You may require a follow-up consultation based on your recent visit."
    blurb = _encounter_blurb_for_patient(encounter)
    if blurb:
        return f"{base}\n\n{blurb}"
    return base


def _logger():
    return frappe.logger("medplum_webhook", allow_site=True)


def _get_secret() -> str | None:
    return (frappe.conf.get("medplum_webhook_secret") or os.getenv("MEDPLUM_WEBHOOK_SECRET") or "").strip() or None


def _get_bearer_token() -> str | None:
    return (frappe.conf.get("medplum_webhook_bearer_token") or os.getenv("MEDPLUM_WEBHOOK_BEARER_TOKEN") or "").strip() or None


def _verify_bearer() -> None:
    expected = _get_bearer_token()
    if not expected:
        return
    auth = frappe.get_request_header("Authorization") or ""
    if auth != f"Bearer {expected}":
        frappe.throw(_("Invalid or missing Authorization"), frappe.AuthenticationError)


def _verify_signature(raw_body: bytes) -> None:
    secret = _get_secret()
    if not secret:
        return
    header = frappe.get_request_header("X-Medplum-Signature") or frappe.request.environ.get(
        MEDPLUM_SIGNATURE_HEADER, ""
    )
    if not header:
        frappe.throw(_("Missing X-Medplum-Signature"), frappe.AuthenticationError)
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, header):
        frappe.throw(_("Invalid webhook signature"), frappe.AuthenticationError)


def _lead_doctype() -> str:
    if frappe.db.exists("DocType", "CRM Lead"):
        return "CRM Lead"
    return "Lead"


def _encounter_status_to_lead_status(enc_status: str | None) -> str:
    mapping = {
        "finished": "Converted",
        "cancelled": "Unqualified",
        "entered-in-error": "Junk",
        "in-progress": "Contacted",
        "arrived": "Contacted",
        "triaged": "Contacted",
        "planned": "New",
        "onleave": "Nurture",
        "unknown": "Nurture",
    }
    return mapping.get((enc_status or "").lower(), "Nurture")


def _unique_medplum_external_id(encounter: dict[str, Any]) -> str:
    """New id on every webhook delivery so the same Encounter can create multiple leads."""
    eid = encounter.get("id")
    if not eid:
        raw = json.dumps(encounter, sort_keys=True, default=str)
        eid = hashlib.sha256(raw.encode()).hexdigest()[:16]
    suffix = uuid.uuid4().hex[:12]
    return f"medplum-Encounter-{eid}-{suffix}"[:140]


def _patient_name(patient: dict[str, Any] | None, subject_display: str | None) -> tuple[str, str, str]:
    if subject_display:
        parts = subject_display.strip().split(None, 1)
        if len(parts) == 2:
            return parts[0], parts[1], subject_display.strip()
        return parts[0], "", subject_display.strip()

    if not patient:
        return "Medplum", "Patient", "Medplum Patient"

    names = patient.get("name") or []
    first = last = ""
    if names:
        n0 = names[0]
        given = n0.get("given") or []
        first = given[0] if given else ""
        last = n0.get("family") or ""
    lead_name = " ".join(x for x in (first, last) if x).strip() or "Medplum Patient"
    return first or "Medplum", last, lead_name


def _patient_phone(patient: dict[str, Any] | None) -> str:
    if not patient:
        return ""
    for t in patient.get("telecom") or []:
        if (t.get("system") or "").lower() in ("phone", "sms", "whatsapp"):
            val = (t.get("value") or "").strip()
            if val:
                return val
    return ""


def _extract_encounter_and_patient(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rtype = payload.get("resourceType")
    if rtype == "Encounter":
        return payload, None
    if rtype == "Bundle":
        encounter = patient = None
        for entry in payload.get("entry") or []:
            res = entry.get("resource") or {}
            rt = res.get("resourceType")
            if rt == "Encounter":
                encounter = res
            elif rt == "Patient":
                patient = res
        return encounter, patient
    return None, None


def _payload_debug_hint(payload: dict[str, Any]) -> str:
    """Short description for Error Log when Medplum POST is not a usable Encounter."""
    rt = payload.get("resourceType")
    if rt == "Bundle":
        types = []
        for entry in (payload.get("entry") or [])[:12]:
            res = entry.get("resource") or {}
            types.append(res.get("resourceType") or "?")
        return f"Bundle type={payload.get('type')!r} entry resourceTypes={types}"
    return f"resourceType={rt!r} id={payload.get('id')!r}"


def _followup_text(encounter: dict[str, Any], patient_label: str) -> str:
    lines: list[str] = [
        f"Medplum: Encounter status is now {encounter.get('status') or 'unknown'}.",
        f"Patient: {patient_label}",
    ]

    for t in encounter.get("type") or []:
        text = t.get("text")
        if not text and t.get("coding"):
            text = (t["coding"][0] or {}).get("display")
        if text:
            lines.append(f"Visit type: {text}")

    for rc in encounter.get("reasonCode") or []:
        txt = rc.get("text")
        if not txt and rc.get("coding"):
            txt = (rc["coding"][0] or {}).get("display")
        if txt:
            lines.append(f"Reason: {txt}")

    for d in encounter.get("diagnosis") or []:
        cond = d.get("condition") or {}
        disp = cond.get("display")
        if disp:
            lines.append(f"Diagnosis: {disp}")

    per = encounter.get("period") or {}
    if per.get("start"):
        lines.append(f"Period: {per.get('start')} → {per.get('end') or '—'}")

    hosp = encounter.get("hospitalization") or {}
    dd = hosp.get("dischargeDisposition")
    if isinstance(dd, dict):
        ddtext = dd.get("text")
        if not ddtext and dd.get("coding"):
            ddtext = (dd["coding"][0] or {}).get("display")
        if ddtext:
            lines.append(f"Disposition: {ddtext}")

    sp = encounter.get("serviceProvider") or {}
    if sp.get("display"):
        lines.append(f"Provider / location: {sp['display']}")

    for ext in encounter.get("extension") or []:
        url = (ext.get("url") or "").lower()
        if any(k in url for k in ("follow", "instruction", "careplan", "procedure")):
            val = ext.get("valueString") or ext.get("valueMarkdown") or ext.get("valueCode")
            if val:
                lines.append(f"Follow-up / procedure: {val}")

    nar = (encounter.get("text") or {}).get("div")
    if isinstance(nar, str) and nar.strip():
        plain = re.sub(r"<[^>]+>", " ", nar)
        plain = re.sub(r"\s+", " ", plain).strip()[:500]
        if plain:
            lines.append(f"Encounter notes: {plain}")

    return "\n".join(lines)


def _create_medplum_lead(
    *,
    doctype: str,
    external_id: str,
    first_name: str,
    last_name: str,
    lead_name: str,
    mobile_no: str,
    status: str,
    summary: str,
) -> frappe.model.document.Document:
    fields: dict[str, Any] = {
        "first_name": (first_name or "Medplum")[:140],
        "last_name": (last_name or "")[:140],
        "facebook_lead_id": external_id[:140],
        "lost_notes": (summary or "")[:2000],
    }

    if doctype == "CRM Lead":
        fields["lead_name"] = (lead_name or fields["first_name"])[:140]
        fields["status"] = status
        fields["naming_series"] = "CRM-LEAD-.YYYY.-"
        if mobile_no:
            fields["mobile_no"] = mobile_no[:40]

    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.insert(ignore_permissions=True)
    return doc


def _send_whatsapp_text(lead_doctype: str, lead_name: str, message: str) -> str | None:
    if not message.strip():
        return None
    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "message": message[:4090],
            "content_type": "text",
            "reference_doctype": lead_doctype,
            "reference_name": lead_name,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def _send_whatsapp_interactive(
    lead_doctype: str, lead_name: str, body: str, buttons: list[dict[str, str]]
) -> str | None:
    if not body.strip() or not buttons:
        return None
    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "message": body[:1024],
            "content_type": "interactive",
            "buttons": json.dumps(buttons),
            "reference_doctype": lead_doctype,
            "reference_name": lead_name,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def _send_medplum_patient_sequence(
    lead_doctype: str, lead_name: str, intro: str
) -> dict[str, str | None]:
    """Match demo flow: info text, then interactive Confirm/Cancel/Reschedule."""
    wa_intro = _send_whatsapp_text(lead_doctype, lead_name, intro)
    wa_prompt = _send_whatsapp_interactive(
        lead_doctype,
        lead_name,
        "Would you like to proceed?",
        MEDPLUM_FOLLOWUP_BUTTONS,
    )
    return {"intro": wa_intro, "prompt": wa_prompt}


@frappe.whitelist(allow_guest=True)
def encounter_webhook():
    """Medplum rest-hook target. Subscription criteria example: `Encounter`."""
    if frappe.request.method == "GET":
        return {"ok": True, "service": "call_intelligence.medplum_webhook"}

    _verify_bearer()
    raw = frappe.request.get_data() or b""
    _verify_signature(raw)

    deleted = frappe.get_request_header("X-Medplum-Deleted-Resource") or frappe.request.environ.get(
        MEDPLUM_DELETED_HEADER
    )
    if deleted:
        _logger().info("medplum deleted resource: %s", deleted)
        return {"ok": True, "ignored": "delete", "resource": deleted}

    if not raw.strip():
        frappe.log_error("POST had empty body (check Medplum Subscription delivery).", "medplum_webhook.empty_body")
        return {"ok": True, "ignored": "empty body"}

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        frappe.throw(_("Invalid JSON body"))

    encounter, bundle_patient = _extract_encounter_and_patient(payload)
    if not encounter:
        hint = _payload_debug_hint(payload)
        _logger().info("medplum webhook ignored (no Encounter): %s", hint)
        frappe.log_error(hint, "medplum_webhook.not_encounter")
        return {"ok": True, "ignored": "not encounter"}

    subject = encounter.get("subject") or {}
    subject_display = subject.get("display")
    patient = bundle_patient
    first_name, last_name, lead_label = _patient_name(patient, subject_display)
    phone = _patient_phone(patient)
    enc_status = encounter.get("status")
    lead_status = _encounter_status_to_lead_status(enc_status)
    external_id = _unique_medplum_external_id(encounter)
    summary = _followup_text(encounter, lead_label)

    doctype = _lead_doctype()
    if doctype != "CRM Lead":
        frappe.throw(_("CRM Lead is required for this integration; install Frappe CRM."))

    try:
        lead = _create_medplum_lead(
            doctype=doctype,
            external_id=external_id,
            first_name=first_name,
            last_name=last_name,
            lead_name=lead_label,
            mobile_no=phone,
            status=lead_status,
            summary=summary,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "call_intelligence.medplum.lead_create")
        raise

    wa_messages: dict[str, str | None] = {}
    try:
        intro = _build_patient_intro(encounter)
        wa_messages = _send_medplum_patient_sequence(doctype, lead.name, intro)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "call_intelligence.medplum.whatsapp")

    _logger().info(
        "medplum encounter processed encounter_id=%s lead=%s wa=%s",
        encounter.get("id"),
        lead.name,
        wa_messages,
    )

    return {
        "ok": True,
        "lead": lead.name,
        "whatsapp_intro": wa_messages.get("intro"),
        "whatsapp_prompt": wa_messages.get("prompt"),
        "encounter_status": enc_status,
    }
