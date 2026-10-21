app_name = "qcs_hrms"
app_title = "QCS HR"
app_publisher = "Quark Cyber System FZC"
app_description = "Standalone fork of Frappe HR"
app_email = "support@quarkcs.com"
app_license = "gpl-3.0"
source_link = "https://github.com/QuarkCyberSystems/qcs_hrms"
app_logo_url = "/assets/qcs_hrms/images/qcs-hr-logo.svg"
app_home = "/desk/hr-setup"

# Apps
# ------------------

# Standalone: runs on Frappe alone (no ERPNext, no Frappe HR)
# required_apps = []

add_to_apps_screen = [
	{
		"name": "qcs_hrms",
		"logo": "/assets/qcs_hrms/images/qcs-hr-logo.svg",
		"title": "QCS HR",
		"route": app_home,
		"has_permission": "qcs_hrms.hr.utils.check_app_permission",
	}
]

# Includes in <head>
# ------------------

app_include_js = [
	"qcs_hrms.bundle.js",
]
app_include_css = "qcs_hrms.bundle.css"
code_only_modules = {"HR": ["HR Setup"]}
app_include_icons = ["/assets/qcs_hrms/icons/module-icons.svg"]

# Home Pages
# ----------

calendars = ["Leave Application"]

# Generators
# ----------

# automatically create page for each record of this doctype
website_generators = ["Job Opening"]

# Jinja
# ----------

jinja = {
	"methods": [
		"qcs_hrms.utils.get_country",
	],
}

# Installation
# ------------

after_install = "qcs_hrms.install.after_install"
after_migrate = "qcs_hrms.setup.update_select_perm_after_install"

# Frappe-only sites have no Company step in the wizard: QCS HR adds one
setup_wizard_requires = "assets/qcs_hrms/js/setup_wizard.js"
setup_wizard_stages = "qcs_hrms.setup_wizard.get_setup_stages"

extend_bootinfo = "qcs_hrms.utils.extend_bootinfo"

# Uninstallation
# ------------

before_uninstall = "qcs_hrms.uninstall.before_uninstall"

# Permissions
# -----------

has_upload_permission = {"Employee": "qcs_hrms.organization.doctype.employee.employee.has_upload_permission"}

# Search queries
# --------------

standard_queries = {"Employee": "qcs_hrms.controllers.queries.employee_query"}

# Document Events
# ---------------
# Employee, Company and Holiday List logic lives in their own controllers (Organization module).

doc_events = {
	"User": {
		"validate": [
			"qcs_hrms.organization.doctype.employee.employee.validate_employee_role",
			"qcs_hrms.organization.doctype.employee.employee.update_approver_user_roles",
		],
	},
	"ToDo": {
		"on_update": "qcs_hrms.controllers.employee_boarding_controller.update_boarding_status_from_todo",
	},
}

# HR Payment references: {"Expense Claim": "dotted.path"}, called with (payment_doc, reference_row, cancel)
# when an HR Payment is submitted/cancelled, so the owning doctype updates its paid amount/status.
qcs_hrms_payment_reference_handlers = {
	"Expense Claim": "qcs_hrms.hr.doctype.expense_claim.expense_claim.update_payment_for_expense_claim",
	"Employee Advance": "qcs_hrms.hr.doctype.employee_advance.employee_advance.update_employee_advance_for_payment",
	"Leave Encashment": "qcs_hrms.hr.doctype.leave_encashment.leave_encashment.update_payment_for_leave_encashment",
	"Full and Final Statement": "qcs_hrms.hr.doctype.full_and_final_statement.full_and_final_statement.update_full_and_final_statement_status",
	"Salary Slip": "qcs_hrms.payroll.doctype.salary_slip.salary_slip.update_payment_reference",
	"Payroll Entry": "qcs_hrms.payroll.doctype.payroll_entry.payroll_entry.update_payment_reference",
	"Gratuity": "qcs_hrms.payroll.doctype.gratuity.gratuity.update_payment_reference",
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"all": [
		"qcs_hrms.hr.doctype.interview.interview.send_interview_reminder",
	],
	"hourly": [
		"qcs_hrms.hr.doctype.daily_work_summary_group.daily_work_summary_group.trigger_emails",
	],
	"hourly_long": [
		"qcs_hrms.hr.doctype.shift_type.shift_type.update_last_sync_of_checkin",
		"qcs_hrms.hr.doctype.shift_type.shift_type.process_auto_attendance_for_all_shifts",
		"qcs_hrms.hr.doctype.shift_schedule_assignment.shift_schedule_assignment.process_auto_shift_creation",
	],
	"daily": [
		"qcs_hrms.controllers.employee_reminders.send_birthday_reminders",
		"qcs_hrms.controllers.employee_reminders.send_work_anniversary_reminders",
		"qcs_hrms.hr.doctype.daily_work_summary_group.daily_work_summary_group.send_summary",
		"qcs_hrms.hr.doctype.interview.interview.send_daily_feedback_reminder",
		"qcs_hrms.hr.doctype.shift_assignment.shift_assignment.mark_expired_shift_assignments_as_inactive",
		"qcs_hrms.hr.doctype.job_opening.job_opening.close_expired_job_openings",
	],
	"daily_long": [
		"qcs_hrms.hr.doctype.leave_ledger_entry.leave_ledger_entry.process_expired_allocation",
		"qcs_hrms.hr.utils.generate_leave_encashment",
		"qcs_hrms.hr.utils.allocate_earned_leaves",
	],
	"weekly": ["qcs_hrms.controllers.employee_reminders.send_reminders_in_advance_weekly"],
	"monthly": ["qcs_hrms.controllers.employee_reminders.send_reminders_in_advance_monthly"],
}

audit_trail_doctypes = ["Expense Claim", "Payroll Entry", "Salary Slip", "Leave Encashment", "Gratuity"]

# Testing
# -------

before_tests = "qcs_hrms.tests.test_utils.before_tests"

# Regional overrides
# ------------------

regional_overrides = {
	"India": {
		"qcs_hrms.hr.utils.calculate_annual_eligible_hra_exemption": "qcs_hrms.regional.india.utils.calculate_annual_eligible_hra_exemption",
		"qcs_hrms.hr.utils.calculate_hra_exemption_for_period": "qcs_hrms.regional.india.utils.calculate_hra_exemption_for_period",
		"qcs_hrms.hr.utils.calculate_tax_with_marginal_relief": "qcs_hrms.regional.india.utils.calculate_tax_with_marginal_relief",
	},
}

# Global Search
# -------------

global_search_doctypes = {
	"Default": [
		{"doctype": "Employee", "index": 18},
		{"doctype": "Salary Slip", "index": 19},
		{"doctype": "Leave Application", "index": 20},
		{"doctype": "Expense Claim", "index": 21},
		{"doctype": "Employee Grade", "index": 37},
		{"doctype": "Job Opening", "index": 39},
		{"doctype": "Job Applicant", "index": 40},
		{"doctype": "Job Offer", "index": 41},
		{"doctype": "Salary Structure Assignment", "index": 42},
		{"doctype": "Appraisal", "index": 43},
	],
}

ignore_links_on_delete = ["PWA Notification"]

# Translation
# --------------------------------

# List of apps whose translatable strings should be excluded from this app's translations.
ignore_translatable_strings_from = ["frappe"]

export_python_type_annotations = True
require_type_annotated_api_methods = True
