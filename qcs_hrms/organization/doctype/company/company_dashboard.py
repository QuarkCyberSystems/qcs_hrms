from frappe import _


def get_data():
	return {
		"fieldname": "company",
		"transactions": [
			{"label": _("Organization"), "items": ["Employee", "Department", "Holiday List Assignment"]},
			{"label": _("Payroll"), "items": ["Salary Structure", "Payroll Entry", "Payroll Period"]},
			{"label": _("Leaves"), "items": ["Leave Period", "Leave Application"]},
			{"label": _("Ledger"), "items": ["HR Ledger Account", "HR Payment"]},
		],
	}
