"""Whitelisted methods for Patient 360 Dashboard and desk integrations."""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _

CI_LQ_COMMENT_KEY = "__call_intelligence_lq__:"
CI_AI_COMMENT_KEY = "__call_intelligence_ai__:"


def _lead_doctype() -> str:
	if frappe.db.exists("DocType", "CRM Lead"):
		return "CRM Lead"
	return "Lead"


def _meta(dt: str):
	return frappe.get_meta(dt)


def _get_field(doc, *names: str) -> Any:
	for n in names:
		if doc.meta.has_field(n):
			v = doc.get(n)
			if v not in (None, ""):
				return v
	return None


def _phone_from_lead(doc) -> str:
	return (doc.get("mobile_no") or doc.get("phone") or doc.get("whatsapp_no") or "").strip()


def _display_name(doc) -> str:
	v = _get_field(doc, "lead_name", "title", "lead_owner")
	if v:
		return str(v)
	parts = [doc.get("first_name") or "", doc.get("last_name") or ""]
	name = " ".join(p for p in parts if p).strip()
	return name or doc.name


def _notes_for_list(doc) -> tuple[str, str]:
	"""lead_notes, ticket_notes snippets for list view."""
	meta = doc.meta
	lead_notes = ""
	if meta.has_field("lead_notes"):
		lead_notes = (doc.get("lead_notes") or "").strip()
	if not lead_notes and meta.has_field("lost_notes"):
		lead_notes = (doc.get("lost_notes") or "").strip()
	if not lead_notes and meta.has_field("description"):
		lead_notes = (doc.get("description") or "").strip()

	ticket_notes = ""
	if meta.has_field("ticket_notes"):
		ticket_notes = (doc.get("ticket_notes") or "").strip()

	return lead_notes[:280], ticket_notes[:280]


def _department_from_doc(doc) -> str:
	v = _get_field(doc, "department", "custom_department", "territory")
	return (str(v).strip() if v else "") or ""


def _priority_from_doc(doc) -> str:
	v = _get_field(doc, "whatsapp_priority", "priority", "lead_priority")
	return (str(v).strip() if v else "") or ""


def _serialize_wa_row(m) -> dict[str, Any]:
	"""Map WhatsApp Message doc to patient_chat.js thread format."""
	content_type = (m.get("content_type") or "text") or "text"
	mt = "text"
	ct = content_type.lower()
	if ct in ("image",):
		mt = "image"
	elif ct in ("document", "file"):
		mt = "document"
	elif ct in ("interactive", "button"):
		mt = "text"

	media = m.get("file_url") or m.get("media_url") or m.get("attachment") or ""
	msg_type = m.get("type") or ""
	direction = "Outgoing" if msg_type == "Outgoing" else "Incoming"

	return {
		"type": msg_type,
		"direction": direction,
		"message": m.get("message") or "",
		"content": m.get("message") or "",
		"creation": str(m.get("creation") or ""),
		"timestamp": str(m.get("creation") or ""),
		"msg_type": mt,
		"media_url": media or None,
		"file_url": media or None,
		"mapping_unknown": 0,
	}


def _whatsapp_for_lead(lead_dt: str, lead_id: str, limit: int = 80) -> list[dict[str, Any]]:
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return []
	rows = frappe.get_all(
		"WhatsApp Message",
		filters={"reference_doctype": lead_dt, "reference_name": lead_id},
		fields=["*"],
		order_by="creation asc",
		limit_page_length=limit,
	)
	out = []
	for r in rows:
		out.append(_serialize_wa_row(r))
	return out


def _read_json_comment(lead_dt: str, lead_id: str, prefix: str) -> dict[str, Any] | None:
	if not frappe.db.exists("DocType", "Comment"):
		return None
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": lead_dt,
			"reference_name": lead_id,
			"comment_type": "Info",
		},
		fields=["content"],
		order_by="creation desc",
		limit_page_length=20,
	)
	for c in comments:
		text = (c.get("content") or "").strip()
		if text.startswith(prefix):
			try:
				return json.loads(text[len(prefix) :])
			except json.JSONDecodeError:
				return None
	return None


def _write_json_comment(lead_dt: str, lead_id: str, prefix: str, payload: dict[str, Any]):
	"""Store JSON in an Info Comment (no schema migration required)."""
	if not frappe.db.exists("DocType", "Comment"):
		return
	content = prefix + json.dumps(payload, default=str)
	existing = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": lead_dt,
			"reference_name": lead_id,
			"comment_type": "Info",
		},
		fields=["name", "content"],
		limit_page_length=50,
	)
	for row in existing:
		if (row.get("content") or "").startswith(prefix):
			frappe.delete_doc("Comment", row.name, force=True, ignore_permissions=True)

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": lead_dt,
			"reference_name": lead_id,
			"content": content[:65000],
		}
	).insert(ignore_permissions=True)


def _list_row(doc) -> dict[str, Any]:
	lead_notes, ticket_notes = _notes_for_list(doc)
	phone = _phone_from_lead(doc)
	src = (doc.get("source") or "").strip() if doc.meta.has_field("source") else ""
	ch = ""
	if src:
		s = src.lower()
		if "whatsapp" in s:
			ch = "WhatsApp"
		elif "web" in s:
			ch = "Website"
		else:
			ch = "Call"

	return {
		"name": doc.name,
		"lead_name": _display_name(doc),
		"phone": phone,
		"lead_notes": lead_notes,
		"ticket_notes": ticket_notes,
		"channel": ch,
		"source": src,
		"department": _department_from_doc(doc),
		"modified": str(doc.modified),
		"priority": _priority_from_doc(doc),
	}


@frappe.whitelist()
def get_patient_360_leads() -> list[dict[str, Any]]:
	dt = _lead_doctype()
	if not frappe.has_permission(dt, "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	fields = ["name", "modified"]
	meta = _meta(dt)
	for fn in (
		"lead_name",
		"first_name",
		"last_name",
		"mobile_no",
		"phone",
		"whatsapp_no",
		"status",
		"source",
		"department",
		"territory",
		"lead_notes",
		"lost_notes",
		"description",
		"ticket_notes",
		"priority",
		"lead_priority",
		"whatsapp_priority",
	):
		if meta.has_field(fn) and fn not in fields:
			fields.append(fn)

	rows = frappe.get_all(dt, fields=fields, order_by="modified desc", limit_page_length=500)
	out: list[dict[str, Any]] = []
	for r in rows:
		doc = frappe.get_doc(dt, r.name)
		out.append(_list_row(doc))
	return out


@frappe.whitelist()
def get_patient_360_leads_with_tickets() -> list[dict[str, Any]]:
	"""Leads that look ticket-backed (non-empty ticket notes or ticket-like status)."""
	full = get_patient_360_leads()
	meta = _meta(_lead_doctype())
	out = []
	for row in full:
		if row.get("ticket_notes"):
			out.append(row)
			continue
		st = ""
		if meta.has_field("status"):
			st = (frappe.db.get_value(_lead_doctype(), row["name"], "status") or "").lower()
		if any(x in st for x in ("support", "ticket", "issue", "escalat", "open")):
			out.append(row)
	if not out:
		return full
	return out


@frappe.whitelist()
def get_whatsapp_communications(lead_name: str, limit: int = 80) -> list[dict[str, Any]]:
	if not lead_name:
		return []
	dt = _lead_doctype()
	if not frappe.has_permission(dt, "read") or not frappe.db.exists(dt, lead_name):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	return _whatsapp_for_lead(dt, lead_name, int(limit or 80))


@frappe.whitelist()
def get_patient_360_data(lead_name: str | None = None) -> dict[str, Any] | None:
	if not lead_name:
		return None
	dt = _lead_doctype()
	if not frappe.has_permission(dt, "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.db.exists(dt, lead_name):
		return None

	doc = frappe.get_doc(dt, lead_name)
	lq = _read_json_comment(dt, lead_name, CI_LQ_COMMENT_KEY) or {}
	ai = _read_json_comment(dt, lead_name, CI_AI_COMMENT_KEY) or {}

	# Fallback AI block from lead fields if present
	if not ai:
		ai = {}
		for fn in ("ai_sentiment", "ai_outcome", "call_summary"):
			if doc.meta.has_field(fn) and doc.get(fn):
				key = "sentiment" if "sentiment" in fn else ("outcome" if "outcome" in fn else "summary")
				ai[key] = doc.get(fn)

	lead = {
		"name": _display_name(doc),
		"lead_id": doc.name,
		"phone": _phone_from_lead(doc) or "—",
		"status": (doc.get("status") or "—") if doc.meta.has_field("status") else "—",
		"booking_status": _get_field(doc, "booking_status", "custom_booking_status") or "—",
		"whatsapp_priority": _priority_from_doc(doc) or "—",
		"department": _department_from_doc(doc) or "—",
		"ai": {
			"sentiment": ai.get("sentiment") or "UNKNOWN",
			"outcome": ai.get("outcome") or "UNKNOWN",
			"summary": ai.get("summary") or "—",
			"action_required": ai.get("action_required") or "—",
			"action_description": ai.get("action_description") or "—",
			"call_solution": ai.get("call_solution") or "—",
		},
		"lq_insight": {
			"qualified": lq.get("qualified"),
			"score": lq.get("score", 0),
			"rationale": lq.get("rationale") or "",
			"diagnosis": lq.get("diagnosis") or "",
			"insurance_status": lq.get("insurance_status") or "",
		},
	}

	issues: list[dict[str, Any]] = []
	if lq.get("issues"):
		issues = lq["issues"][:3]

	return {
		"lead": lead,
		"issues": issues,
		"whatsapp_messages": _whatsapp_for_lead(dt, doc.name, 80),
	}


def _simple_qualification(doc) -> dict[str, Any]:
	"""Lightweight deterministic qualification for demos (no external LLM)."""
	text = " ".join(
		filter(
			None,
			[
				str(doc.get("lead_notes") or ""),
				str(doc.get("lost_notes") or ""),
				str(doc.get("description") or ""),
				str(doc.get("lead_name") or ""),
			],
		)
	).lower()
	score = 0
	if any(k in text for k in ("surgery", "emergency", "urgent", "pain", "admit")):
		score += 40
	if any(k in text for k in ("follow", "review", "checkup", "appointment")):
		score += 25
	if any(k in text for k in ("insurance", "cash", "payment")):
		score += 15
	if len(text) > 40:
		score += 10
	qualified = score >= 55
	rationale = (
		"Raised score based on clinical urgency / follow-up cues in lead notes (demo heuristic)."
		if qualified
		else "Insufficient structured urgency cues in notes for automatic qualification (demo heuristic)."
	)
	return {
		"qualified": qualified,
		"score": min(score, 100),
		"rationale": rationale,
		"diagnosis": "",
		"insurance_status": "",
		"issues": [],
	}


@frappe.whitelist()
def run_lead_qualification(lead_name: str | None = None) -> dict[str, Any]:
	if not lead_name:
		frappe.throw(_("lead_name required"))
	dt = _lead_doctype()
	if not frappe.has_permission(dt, "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	doc = frappe.get_doc(dt, lead_name)
	res = _simple_qualification(doc)
	_write_json_comment(dt, lead_name, CI_LQ_COMMENT_KEY, res)
	try:
		frappe.publish_realtime("lead_qualification_updated", {"lead_name": lead_name}, after_commit=True)
	except Exception:
		pass
	return {"result": res, "ok": True}


@frappe.whitelist()
def create_demo_patient() -> dict[str, Any]:
	dt = _lead_doctype()
	if not frappe.has_permission(dt, "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if dt != "CRM Lead":
		frappe.throw(_("Install Frappe CRM (CRM Lead) for the Patient 360 demo flow."))

	fields = {
		"doctype": "CRM Lead",
		"lead_name": "Demo Patient",
		"first_name": "Demo",
		"last_name": "Patient",
		"status": "New",
		"mobile_no": "+10000000000",
		"source": "Call Intelligence Demo",
		"lead_notes": "Demo lead created from Patient 360 Dashboard.",
	}
	meta = _meta("CRM Lead")
	if meta.has_field("naming_series"):
		fields["naming_series"] = "CRM-LEAD-.YYYY.-"
	doc = frappe.get_doc(fields)
	doc.insert()
	return {"lead_name": doc.name, "phone": doc.get("mobile_no") or ""}


@frappe.whitelist()
def send_demo_whatsapp_message(lead_name: str | None = None) -> dict[str, Any]:
	if not lead_name:
		frappe.throw(_("lead_name required"))
	dt = _lead_doctype()
	if not frappe.has_permission(dt, "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return {"ok": False, "error": "frappe_whatsapp not installed"}

	msg = (
		"Hello from Call Intelligence — this is a demo outbound WhatsApp message queued via WhatsApp Message."
	)
	doc = frappe.get_doc(
		{
			"doctype": "WhatsApp Message",
			"type": "Outgoing",
			"message": msg[:4090],
			"content_type": "text",
			"reference_doctype": dt,
			"reference_name": lead_name,
		}
	)
	doc.insert(ignore_permissions=True)
	return {"ok": True, "name": doc.name}


@frappe.whitelist()
def delete_patient_360_lead(lead_name: str | None = None) -> dict[str, Any]:
	if not lead_name:
		frappe.throw(_("lead_name required"))
	dt = _lead_doctype()
	if not frappe.has_permission(dt, "delete"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	frappe.delete_doc(dt, lead_name, ignore_permissions=True)
	return {"ok": True}


def _try_frappe_whatsapp_send(
	*,
	phone: str,
	reference_doctype: str,
	reference_name: str,
	msg_type: str,
	message: str | None,
	media_url: str | None,
	caption: str | None,
	filename: str | None,
) -> dict[str, Any]:
	try:
		fields = {
			"doctype": "WhatsApp Message",
			"type": "Outgoing",
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		}
		to_field = "to" if frappe.get_meta("WhatsApp Message").has_field("to") else None
		if to_field:
			fields[to_field] = phone

		mt = (msg_type or "text").lower()
		if mt == "text":
			fields["content_type"] = "text"
			fields["message"] = (message or "")[:4090]
		else:
			fields["content_type"] = mt if mt in ("image", "document", "audio", "video") else "document"
			fields["message"] = (caption or message or "")[:4090]
			if frappe.get_meta("WhatsApp Message").has_field("media_url"):
				fields["media_url"] = media_url
			if frappe.get_meta("WhatsApp Message").has_field("file_url"):
				fields["file_url"] = media_url

		doc = frappe.get_doc(fields)
		if hasattr(doc, "send") and callable(doc.send):
			doc.insert(ignore_permissions=True)
			doc.send()
		else:
			doc.insert(ignore_permissions=True)

		return {"ok": True, "note": _("Message queued for WhatsApp.")}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "call_intelligence.api.send_whatsapp_message")
		return {"ok": False, "error_hint": _("Could not queue WhatsApp message. Check Error Log.")}


@frappe.whitelist()
def send_whatsapp_message(
	phone: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	msg_type: str | None = "text",
	message: str | None = None,
	media_url: str | None = None,
	caption: str | None = None,
	filename: str | None = None,
) -> dict[str, Any]:
	if not reference_doctype or not reference_name:
		frappe.throw(_("reference_doctype and reference_name required"))
	if not frappe.has_permission(reference_doctype, "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return {"ok": False, "error_hint": _("Install frappe_whatsapp.")}

	phone = (phone or "").strip()
	if not phone:
		frappe.throw(_("Phone required"))

	return _try_frappe_whatsapp_send(
		phone=phone,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		msg_type=msg_type or "text",
		message=message,
		media_url=media_url,
		caption=caption,
		filename=filename,
	)
