"""Post-install hooks: ensure Call Intelligence workspace exists (sidebar)."""

from __future__ import annotations

import frappe


def after_install():
	ensure_call_intelligence_workspace()


def ensure_call_intelligence_workspace():
	"""Create the public workspace if fixtures were not imported yet (sidebar)."""
	if not frappe.db.exists("DocType", "Workspace"):
		return

	name = "Call Intelligence"
	if frappe.db.exists("Workspace", name):
		return

	shortcuts = [
		{
			"doctype": "Workspace Shortcut",
			"type": "Page",
			"link_to": "patient-360-dashboard",
			"label": "Patient 360 Dashboard",
		}
	]

	doc = frappe.get_doc(
		{
			"doctype": "Workspace",
			"label": name,
			"title": name,
			"type": "Workspace",
			"module": "Call Intelligence",
			"app": "call_intelligence",
			"icon": "phone",
			"is_hidden": 0,
			"sequence_id": 80.0,
			"shortcuts": shortcuts,
		}
	)
	doc.insert(ignore_permissions=True)
