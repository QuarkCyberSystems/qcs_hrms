import os

import frappe
from frappe.desk.page.setup_wizard.install_fixtures import (
	_,  # NOTE: this is not the real translation function
)
from frappe.desk.page.setup_wizard.setup_wizard import make_records
from frappe.permissions import add_permission, update_permission_property

from qcs_hrms.overrides.company import delete_company_fixtures, make_salary_components, run_regional_setup


def after_install():
	"""Install HR fixtures and defaults. Idempotent: safe on a site that is already set up."""
	make_fixtures()
	setup_notifications()
	update_hr_defaults()
	add_non_standard_user_types()
	set_single_defaults()
	create_default_role_profiles()
	create_country_fixtures()
	add_default_hr_permissions()
	set_hrms_as_default_app()


def before_uninstall():
	delete_company_fixtures()


def set_hrms_as_default_app(*args):
	if not frappe.get_system_settings("default_app"):
		frappe.db.set_single_value("System Settings", "default_app", "qcs_hrms")


def create_country_fixtures():
	"""Regional setup and salary components for companies that already exist (e.g. on reinstall)."""
	for country in frappe.get_all("Company", pluck="country", distinct=True):
		if country:
			run_regional_setup(country)
			make_salary_components(country)


def make_fixtures():
	records = [
		# expense claim type
		{"doctype": "Expense Claim Type", "name": _("Calls"), "expense_type": _("Calls")},
		{"doctype": "Expense Claim Type", "name": _("Food"), "expense_type": _("Food")},
		{"doctype": "Expense Claim Type", "name": _("Medical"), "expense_type": _("Medical")},
		{"doctype": "Expense Claim Type", "name": _("Others"), "expense_type": _("Others")},
		{"doctype": "Expense Claim Type", "name": _("Travel"), "expense_type": _("Travel")},
		# vehicle service item
		{"doctype": "Vehicle Service Item", "service_item": "Brake Oil"},
		{"doctype": "Vehicle Service Item", "service_item": "Brake Pad"},
		{"doctype": "Vehicle Service Item", "service_item": "Clutch Plate"},
		{"doctype": "Vehicle Service Item", "service_item": "Engine Oil"},
		{"doctype": "Vehicle Service Item", "service_item": "Oil Change"},
		{"doctype": "Vehicle Service Item", "service_item": "Wheels"},
		# leave type
		{
			"doctype": "Leave Type",
			"leave_type_name": _("Casual Leave"),
			"name": _("Casual Leave"),
			"allow_encashment": 1,
			"is_carry_forward": 1,
			"max_continuous_days_allowed": "3",
			"include_holiday": 1,
		},
		{
			"doctype": "Leave Type",
			"leave_type_name": _("Compensatory Off"),
			"name": _("Compensatory Off"),
			"allow_encashment": 0,
			"is_carry_forward": 0,
			"include_holiday": 1,
			"is_compensatory": 1,
		},
		{
			"doctype": "Leave Type",
			"leave_type_name": _("Sick Leave"),
			"name": _("Sick Leave"),
			"allow_encashment": 0,
			"is_carry_forward": 0,
			"include_holiday": 1,
		},
		{
			"doctype": "Leave Type",
			"leave_type_name": _("Privilege Leave"),
			"name": _("Privilege Leave"),
			"allow_encashment": 0,
			"is_carry_forward": 0,
			"include_holiday": 1,
		},
		{
			"doctype": "Leave Type",
			"leave_type_name": _("Leave Without Pay"),
			"name": _("Leave Without Pay"),
			"allow_encashment": 0,
			"is_carry_forward": 0,
			"is_lwp": 1,
			"include_holiday": 1,
		},
		# Employment Type
		{"doctype": "Employment Type", "employee_type_name": _("Full-time")},
		{"doctype": "Employment Type", "employee_type_name": _("Part-time")},
		{"doctype": "Employment Type", "employee_type_name": _("Probation")},
		{"doctype": "Employment Type", "employee_type_name": _("Contract")},
		{"doctype": "Employment Type", "employee_type_name": _("Commission")},
		{"doctype": "Employment Type", "employee_type_name": _("Piecework")},
		{"doctype": "Employment Type", "employee_type_name": _("Intern")},
		{"doctype": "Employment Type", "employee_type_name": _("Apprentice")},
		# Job Applicant Source
		{"doctype": "Job Applicant Source", "source_name": _("Website Listing")},
		{"doctype": "Job Applicant Source", "source_name": _("Walk In")},
		{"doctype": "Job Applicant Source", "source_name": _("Employee Referral")},
		{"doctype": "Job Applicant Source", "source_name": _("Campaign")},
		# Offer Term
		{"doctype": "Offer Term", "offer_term": _("Date of Joining")},
		{"doctype": "Offer Term", "offer_term": _("Annual Salary")},
		{"doctype": "Offer Term", "offer_term": _("Probationary Period")},
		{"doctype": "Offer Term", "offer_term": _("Employee Benefits")},
		{"doctype": "Offer Term", "offer_term": _("Working Hours")},
		{"doctype": "Offer Term", "offer_term": _("Stock Options")},
		{"doctype": "Offer Term", "offer_term": _("Department")},
		{"doctype": "Offer Term", "offer_term": _("Job Description")},
		{"doctype": "Offer Term", "offer_term": _("Responsibilities")},
		{"doctype": "Offer Term", "offer_term": _("Leaves per Year")},
		{"doctype": "Offer Term", "offer_term": _("Notice Period")},
		{"doctype": "Offer Term", "offer_term": _("Incentives")},
		# Email Account
		{"doctype": "Email Account", "email_id": "jobs@example.com", "append_to": "Job Applicant"},
	]

	make_records(records)


def setup_notifications():
	base_path = frappe.get_app_path("qcs_hrms", "hr", "doctype")

	# Leave Application
	response = frappe.read_file(
		os.path.join(base_path, "leave_application/leave_application_email_template.html")
	)
	records = [
		{
			"doctype": "Email Template",
			"name": _("Leave Approval Notification"),
			"response": response,
			"subject": _("Leave Approval Notification"),
			"owner": frappe.session.user,
		}
	]
	records += [
		{
			"doctype": "Email Template",
			"name": _("Leave Status Notification"),
			"response": response,
			"subject": _("Leave Status Notification"),
			"owner": frappe.session.user,
		}
	]

	# Interview
	response = frappe.read_file(
		os.path.join(base_path, "interview/interview_reminder_notification_template.html")
	)
	records += [
		{
			"doctype": "Email Template",
			"name": _("Interview Reminder"),
			"response": response,
			"subject": _("Interview Reminder"),
			"owner": frappe.session.user,
		}
	]
	response = frappe.read_file(
		os.path.join(base_path, "interview/interview_feedback_reminder_template.html")
	)
	records += [
		{
			"doctype": "Email Template",
			"name": _("Interview Feedback Reminder"),
			"response": response,
			"subject": _("Interview Feedback Reminder"),
			"owner": frappe.session.user,
		}
	]

	# Exit Interview
	response = frappe.read_file(
		os.path.join(base_path, "exit_interview/exit_questionnaire_notification_template.html")
	)
	records += [
		{
			"doctype": "Email Template",
			"name": _("Exit Questionnaire Notification"),
			"response": response,
			"subject": _("Exit Questionnaire Notification"),
			"owner": frappe.session.user,
		}
	]

	make_records(records)


def update_hr_defaults():
	"""Fill HR Settings defaults, leaving values an existing site already configured untouched."""
	hr_settings = frappe.get_doc("HR Settings")
	defaults = {
		"emp_created_by": "Naming Series",
		"leave_approval_notification_template": _("Leave Approval Notification"),
		"leave_status_notification_template": _("Leave Status Notification"),
		"interview_reminder_template": _("Interview Reminder"),
		"remind_before": "00:15:00",
		"feedback_reminder_notification_template": _("Interview Feedback Reminder"),
		"exit_questionnaire_notification_template": _("Exit Questionnaire Notification"),
	}
	for fieldname, value in defaults.items():
		if not hr_settings.get(fieldname):
			hr_settings.set(fieldname, value)

	if hr_settings.is_new() or not frappe.db.get_single_value("HR Settings", "emp_created_by"):
		hr_settings.send_interview_reminder = 1
		hr_settings.send_interview_feedback_reminder = 1

	hr_settings.flags.ignore_mandatory = True
	hr_settings.save()


def set_single_defaults():
	for dt in ("HR Settings", "Payroll Settings"):
		default_values = frappe.get_all(
			"DocField",
			filters={"parent": dt},
			fields=["fieldname", "default"],
			as_list=True,
		)
		if default_values:
			try:
				doc = frappe.get_doc(dt, dt)
				for fieldname, value in default_values:
					# only fill blanks: never overwrite settings of an already set-up site
					if value is not None and doc.get(fieldname) in (None, ""):
						doc.set(fieldname, value)
				doc.flags.ignore_mandatory = True
				doc.save()
			except frappe.ValidationError:
				pass


def create_default_role_profiles():
	for role_profile_name, roles in DEFAULT_ROLE_PROFILES.items():
		if frappe.db.exists("Role Profile", role_profile_name):
			continue

		role_profile = frappe.new_doc("Role Profile")
		role_profile.role_profile = role_profile_name
		for role in roles:
			role_profile.append("roles", {"role": role})

		role_profile.insert(ignore_permissions=True)


# ESS USER TYPE SETUP & CLEANUP
def add_non_standard_user_types():
	user_types = get_user_types_data()

	for user_type, data in user_types.items():
		create_custom_role(data)
		create_user_type(user_type, data)


def get_user_types_data():
	return {
		"Employee Self Service": {
			"role": "Employee Self Service",
			"apply_user_permission_on": "Employee",
			"user_id_field": "user_id",
			"doctypes": {
				# masters
				"Holiday List": ["read"],
				"Employee": ["read", "write"],
				"Company": ["read"],
				# payroll
				"Salary Slip": ["read"],
				"Employee Benefit Application": ["read", "write", "create", "delete"],
				# expenses
				"Expense Claim": ["read", "write", "create", "delete"],
				"Expense Claim Type": ["read"],
				"Employee Advance": ["read", "write", "create", "delete"],
				"HR Payment": ["read"],
				# leave and attendance
				"Leave Type": ["read"],
				"Leave Application": ["read", "write", "create", "delete"],
				"Attendance Request": ["read", "write", "create", "delete"],
				"Compensatory Leave Request": ["read", "write", "create", "delete"],
				# tax
				"Employee Tax Exemption Declaration": ["read", "write", "create", "delete"],
				"Employee Tax Exemption Proof Submission": ["read", "write", "create", "delete"],
				# trainings
				"Training Program": ["read"],
				"Training Feedback": ["read", "write", "create", "delete", "submit", "cancel", "amend"],
				# shifts
				"Employee Checkin": ["read"],
				"Shift Request": ["read", "write", "create", "delete", "submit", "cancel", "amend"],
				# misc
				"Employee Grievance": ["read", "write", "create", "delete"],
				"Employee Referral": ["read", "write", "create", "delete"],
				"Travel Request": ["read", "write", "create", "delete"],
			},
		}
	}


def create_custom_role(data):
	if data.get("role") and not frappe.db.exists("Role", data.get("role")):
		frappe.get_doc(
			{"doctype": "Role", "role_name": data.get("role"), "desk_access": 1, "is_custom": 1}
		).insert(ignore_permissions=True)


def create_user_type(user_type, data):
	if frappe.db.exists("User Type", user_type):
		doc = frappe.get_cached_doc("User Type", user_type)
		doc.user_doctypes = []
	else:
		doc = frappe.new_doc("User Type")
		doc.update(
			{
				"name": user_type,
				"role": data.get("role"),
				"user_id_field": data.get("user_id_field"),
				"apply_user_permission_on": data.get("apply_user_permission_on"),
			}
		)

	docperms = data.get("doctypes")

	append_docperms_to_user_type(docperms, doc)

	doc.flags.ignore_links = True
	doc.save(ignore_permissions=True)


def append_docperms_to_user_type(docperms, doc):
	existing_doctypes = [d.document_type for d in doc.user_doctypes]

	for doctype, perms in docperms.items():
		if doctype in existing_doctypes:
			continue

		args = {"document_type": doctype}
		for perm in perms:
			args[perm] = 1

		doc.append("user_doctypes", args)


def update_select_perm_after_install():
	if not frappe.flags.update_select_perm_after_migrate:
		return

	frappe.flags.ignore_select_perm = False
	for row in frappe.get_all("User Type", filters={"is_standard": 0}):
		print("Updating user type :- ", row.name)
		doc = frappe.get_doc("User Type", row.name)
		doc.flags.ignore_links = True
		doc.save()

	frappe.flags.update_select_perm_after_migrate = False


DEFAULT_ROLE_PROFILES = {
	"HR": [
		"HR User",
		"HR Manager",
		"Leave Approver",
		"Expense Approver",
	],
}


def delete_custom_fields(custom_fields: dict):
	"""
	:param custom_fields: a dict like `{'Salary Slip': [{fieldname: 'loans', ...}]}`
	"""
	for doctype, fields in custom_fields.items():
		frappe.db.delete(
			"Custom Field",
			{
				"fieldname": ("in", [field["fieldname"] for field in fields]),
				"dt": doctype,
			},
		)

		frappe.clear_cache(doctype=doctype)


# Add default permission for hr roles
def add_default_hr_permissions():
	role_permissions = {
		"HR User": {
			"Role": {"read": 1},
			"Currency": {"read": 1},
		},
		"HR Manager": {
			"Role": {"read": 1},
			"Currency": {"read": 1},
			"Email Account": {"read": 1},
		},
	}

	for role, permissions in role_permissions.items():
		for doctype, ptypes in permissions.items():
			add_permission(doctype, role)

			for ptype, value in ptypes.items():
				update_permission_property(doctype, role, permlevel=0, ptype=ptype, value=value)
