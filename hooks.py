app_name = "call_intelligence"
app_title = "Call Intelligence"
app_publisher = "Anjali"
app_description = "Patient communication and qualification tools"
app_email = "contact@example.com"
app_license = "mit"

add_to_apps_screen = [
	{
		"name": "call_intelligence",
		"logo": "/assets/frappe/images/frappe-framework-logo.svg",
		"title": "Call Intelligence",
		"route": "/app/patient-360-dashboard",
	}
]

after_install = "call_intelligence.setup.install.after_install"

fixtures = [
	{"dt": "Workspace", "filters": [["name", "=", "Call Intelligence"]]},
]

doc_events = {
	"WhatsApp Message": {
		"after_insert": "call_intelligence.integrations.whatsapp_bridge.whatsapp_message_after_insert",
		"on_update": "call_intelligence.integrations.whatsapp_bridge.sync_whatsapp_to_communication",
	}
}

override_doctype_class = {
	"WhatsApp Message": "call_intelligence.overrides.whatsapp_message.WhatsAppMessage",
}

page_js = {"patient-360-dashboard": "public/js/patient_chat.js"}

# app_include_js = []  # use page_js for the Patient 360 page only
