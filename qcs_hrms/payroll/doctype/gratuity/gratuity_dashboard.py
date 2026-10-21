from frappe import _


def get_data():
	return {
		"fieldname": "reference_name",
		"non_standard_fieldnames": {
			"Additional Salary": "ref_docname",
			"HR Ledger Entry": "voucher_no",
		},
		"transactions": [
			{"label": _("Payment"), "items": ["HR Payment", "Additional Salary"]},
			{"label": _("Ledger"), "items": ["HR Ledger Entry"]},
		],
	}
