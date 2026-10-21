def get_data():
	return {
		"fieldname": "payroll_entry",
		"non_standard_fieldnames": {
			"HR Ledger Entry": "voucher_no",
		},
		"transactions": [{"items": ["Salary Slip", "HR Ledger Entry"]}],
	}
