from frappe import _


def get_data():
	return {
		"fieldname": "reference_name",
		"internal_links": {"Employee Advance": ["advances", "employee_advance"]},
		"transactions": [
			{"label": _("Reference"), "items": ["Employee Advance"]},
		],
	}
