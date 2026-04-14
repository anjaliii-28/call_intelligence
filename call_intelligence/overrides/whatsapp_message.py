"""Extend frappe_whatsapp WhatsApp Message when that app is installed."""

import frappe


def _base_class():
	try:
		from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
			WhatsAppMessage as Base,
		)

		return Base
	except Exception:
		return frappe.model.document.Document


class WhatsAppMessage(_base_class()):
	pass
