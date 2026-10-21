from frappe import _


def get_data():
	return {
		"heatmap": True,
		"heatmap_message": _("This is based on the attendance of this Employee"),
		"fieldname": "employee",
		"method": "qcs_hrms.organization.doctype.employee.employee.get_timeline_data",
		"non_standard_fieldnames": {
			"Employee Grievance": "raised_by",
			"Holiday List Assignment": "assigned_to",
			"HR Payment": "party",
		},
		"dynamic_links": {
			"assigned_to": ["Employee", "applicable_for"],
			"party": ["Employee", "party_type"],
		},
		"transactions": [
			{"label": _("Attendance"), "items": ["Attendance", "Attendance Request", "Employee Checkin"]},
			{
				"label": _("Leave"),
				"items": [
					"Leave Application",
					"Leave Allocation",
					"Leave Policy Assignment",
					"Holiday List Assignment",
				],
			},
			{
				"label": _("Lifecycle"),
				"items": [
					"Employee Onboarding",
					"Employee Transfer",
					"Employee Promotion",
					"Employee Grievance",
				],
			},
			{
				"label": _("Employee Exit"),
				"items": [
					"Employee Separation",
					"Exit Interview",
					"Full and Final Statement",
					"Salary Withholding",
				],
			},
			{"label": _("Shift"), "items": ["Shift Request", "Shift Assignment"]},
			{"label": _("Expense"), "items": ["Expense Claim", "Travel Request", "Employee Advance", "HR Payment"]},
			{"label": _("Benefit"), "items": ["Employee Benefit Application", "Employee Benefit Claim"]},
			{
				"label": _("Payroll"),
				"items": [
					"Salary Structure Assignment",
					"Salary Slip",
					"Additional Salary",
					"Employee Incentive",
					"Retention Bonus",
					"Overtime Slip",
					"Arrear",
					"Payroll Correction",
				],
			},
			{
				"label": _("Training"),
				"items": ["Training Event", "Training Result", "Training Feedback", "Employee Skill Map"],
			},
			{"label": _("Evaluation"), "items": ["Appraisal"]},
		],
	}
