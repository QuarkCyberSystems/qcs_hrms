import json
import os
import unittest
from contextlib import contextmanager

import frappe
from frappe.utils import getdate

from qcs_hrms.utils.compat import get_fiscal_year


class BootStrapTestData:
	def __init__(self):
		self.make_presets()
		self.make_master_data()

	def make_presets(self):
		from frappe.desk.page.setup_wizard.install_fixtures import update_genders, update_salutations

		update_genders()
		update_salutations()
		self.update_system_settings()
		self.make_designations()
		self.make_roles()
		self.make_users()

	def make_master_data(self):
		self.make_test_holiday_list()
		self.make_company()
		self.make_departments()
		self.make_employees()
		self.make_exchange_rate()
		self.make_holiday_list()
		self.make_holiday_list_assignment()
		self.make_salary_components()
		self.make_leave_types()
		self.make_leave_period()
		self.make_leave_block_lists()
		self.make_leave_allocations()
		self.make_leave_applications()
		self.update_email_account_settings()
		self.update_system_settings()
		self.update_hr_settings()
		# TODO: clean up
		if frappe.db.get_value("Holiday List Assignment", {"assigned_to": "_Test Company"}, "docstatus") == 0:
			frappe.get_doc("Holiday List Assignment", {"assigned_to": "_Test Company"}).submit()
		frappe.db.commit()  # nosemgrep

	def make_designations(self):
		designations = [
			"Engineer",
			"Project Manager",
			"Researcher",
			"Accountant",
			"Manager",
			"Software Developer",
			"UX Designer",
			"Designer",
		]
		records = [{"doctype": "Designation", "designation_name": x} for x in designations]
		self.make_records(["designation_name"], records)

	def make_exchange_rate(self):
		records = [
			{
				"doctype": "Currency Exchange",
				"date": "2016-01-01",
				"exchange_rate": 60.0,
				"from_currency": "USD",
				"to_currency": "INR",
				"for_buying": 1,
				"for_selling": 0,
			},
			{
				"doctype": "Currency Exchange",
				"date": "2016-01-10",
				"exchange_rate": 65.1,
				"from_currency": "USD",
				"to_currency": "INR",
				"for_buying": 1,
				"for_selling": 0,
			},
			{
				"doctype": "Currency Exchange",
				"date": "2016-01-30",
				"exchange_rate": 62.9,
				"from_currency": "USD",
				"to_currency": "INR",
				"for_buying": 1,
				"for_selling": 1,
			},
		]
		self.make_records(["date", "from_currency", "to_currency"], records)

	def make_salary_components(self):
		records = [
			{
				"doctype": "Salary Component",
				"salary_component": "_Test Basic Salary",
				"type": "Earning",
				"is_tax_applicable": 1,
			},
			{
				"doctype": "Salary Component",
				"salary_component": "_Test Allowance",
				"type": "Earning",
				"is_tax_applicable": 1,
			},
			{
				"doctype": "Salary Component",
				"salary_component": "_Test Professional Tax",
				"type": "Deduction",
			},
			{"doctype": "Salary Component", "salary_component": "_Test TDS", "type": "Deduction"},
			{
				"doctype": "Salary Component",
				"salary_component": "Basic",
				"type": "Earning",
				"is_tax_applicable": 1,
			},
			{
				"doctype": "Salary Component",
				"salary_component": "Leave Encashment",
				"type": "Earning",
				"is_tax_applicable": 1,
			},
		]
		self.make_records(["salary_component"], records)

	def make_company(self):
		path = frappe.get_app_path("qcs_hrms", "organization", "doctype", "company", "test_records.json")
		with open(path) as f:
			records = json.load(f)
		# parents before children, so the nested set can be built
		self.make_records(["company_name"], [r for r in records if not r.get("parent_company")])
		self.make_records(["company_name"], [r for r in records if r.get("parent_company")])

	def make_test_holiday_list(self):
		records = [
			{
				"doctype": "Holiday List",
				"from_date": "2013-01-01",
				"to_date": "2013-12-31",
				"holidays": [
					{"description": "New Year", "holiday_date": "2013-01-01"},
					{"description": "Republic Day", "holiday_date": "2013-01-26"},
					{"description": "Test Holiday", "holiday_date": "2013-02-01"},
				],
				"holiday_list_name": "_Test Holiday List",
			}
		]
		self.make_records(["holiday_list_name"], records)

	def make_departments(self):
		records = [
			{
				"doctype": "Department",
				"department_name": "_Test Department",
				"company": "_Test Company",
				"parent_department": "All Departments",
			},
			{
				"doctype": "Department",
				"department_name": "_Test Department 1",
				"company": "_Test Company",
				"parent_department": "All Departments",
			},
		]
		self.make_records(["department_name"], records)

	def make_roles(self):
		records = [
			{"doctype": "Role", "role_name": "_Test Role", "desk_access": 1},
			{"doctype": "Role", "role_name": "_Test Role 2", "desk_access": 1},
			{"doctype": "Role", "role_name": "_Test Role 3", "desk_access": 1},
			{"doctype": "Role", "role_name": "_Test Role 4", "desk_access": 0},
		]
		self.make_records(["role_name"], records)

	def make_users(self):
		records = [
			{
				"doctype": "User",
				"email": "test@example.com",
				"enabled": 1,
				"first_name": "_Test",
				"new_password": "Eastern_43A1W",
				"send_welcome_email": 0,
				"roles": [
					{"doctype": "Has Role", "parentfield": "roles", "role": "_Test Role"},
					{"doctype": "Has Role", "parentfield": "roles", "role": "System Manager"},
				],
			},
		]
		for idx in range(1, 5):
			records.append(
				{
					"doctype": "User",
					"email": f"test{idx}@example.com",
					"first_name": f"_Test{idx}",
					"new_password": "Eastern_43A1W",
					"send_welcome_email": 0,
					"enabled": 1,
				}
			)
		records.append(
			{
				"doctype": "User",
				"email": "testperm@example.com",
				"first_name": "_Test Perm",
				"new_password": "Eastern_43A1W",
				"send_welcome_email": 0,
				"enabled": 1,
			}
		)
		self.make_records(["email"], records)

	def make_employees(self):
		records = [
			{
				"company": "_Test Company",
				"date_of_birth": "1980-01-01",
				"date_of_joining": "2010-01-01",
				"department": "_Test Department - _TC",
				"doctype": "Employee",
				"first_name": "_Test Employee",
				"gender": "Female",
				"naming_series": "_T-Employee-",
				"status": "Active",
				"user_id": "test@example.com",
			},
			{
				"company": "_Test Company",
				"date_of_birth": "1980-01-01",
				"date_of_joining": "2010-01-01",
				"department": "_Test Department 1 - _TC",
				"doctype": "Employee",
				"first_name": "_Test Employee 1",
				"gender": "Male",
				"naming_series": "_T-Employee-",
				"status": "Active",
				"user_id": "test1@example.com",
			},
			{
				"company": "_Test Company",
				"date_of_birth": "1980-01-01",
				"date_of_joining": "2010-01-01",
				"department": "_Test Department 1 - _TC",
				"doctype": "Employee",
				"first_name": "_Test Employee 2",
				"gender": "Male",
				"naming_series": "_T-Employee-",
				"status": "Active",
				"user_id": "test2@example.com",
			},
		]
		self.make_records(["first_name"], records)

	def make_holiday_list_assignment(self):
		fiscal_year = get_fiscal_year(getdate())
		records = [
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Company",
				"assigned_to": "_Test Company",
				"holiday_list": "Salary Slip Test Holiday List",
				"from_date": fiscal_year[1],
				"to_date": fiscal_year[2],
			}
		]
		self.make_records(["assigned_to", "from_date"], records)

	def make_holiday_list(self):
		fiscal_year = get_fiscal_year(getdate())
		records = [
			{
				"doctype": "Holiday List",
				"from_date": fiscal_year[1],
				"to_date": fiscal_year[2],
				"holiday_list_name": "Salary Slip Test Holiday List",
				"weekly_off": "Sunday",
			}
		]
		self.make_records(["from_date", "to_date", "holiday_list_name"], records)

	def make_leave_types(self):
		"""Create test leave types"""
		# Create test leave types here
		records = [
			{"doctype": "Leave Type", "leave_type_name": "_Test Leave Type", "include_holiday": 1},
			{
				"doctype": "Leave Type",
				"is_lwp": 1,
				"leave_type_name": "_Test Leave Type LWP",
				"include_holiday": 1,
			},
			{
				"doctype": "Leave Type",
				"leave_type_name": "_Test Leave Type Encashment",
				"include_holiday": 1,
				"allow_encashment": 1,
				"non_encashable_leaves": 5,
				"earning_component": "Leave Encashment",
			},
			{
				"doctype": "Leave Type",
				"leave_type_name": "_Test Leave Type Earned",
				"include_holiday": 1,
				"is_earned_leave": 1,
			},
		]
		self.make_records(["leave_type_name"], records)

	def make_leave_period(self):
		records = [
			{
				"doctype": "Leave Period",
				"company": "_Test Company",
				"from_date": "2013-01-01",
				"to_date": "2019-12-31",
			}
		]
		self.make_records(["from_date", "to_date", "company"], records)

	def make_leave_allocations(self):
		"""Create test leave applications"""
		# Create test leave applications here
		records = [
			{
				"docstatus": 1,
				"doctype": "Leave Allocation",
				"employee": "_T-Employee-00001",
				"from_date": "2013-01-01",
				"to_date": "2019-12-31",
				"leave_type": "_Test Leave Type",
				"new_leaves_allocated": 15,
			},
			{
				"docstatus": 1,
				"doctype": "Leave Allocation",
				"employee": "_T-Employee-00002",
				"from_date": "2013-01-01",
				"to_date": "2013-12-31",
				"leave_type": "_Test Leave Type",
				"new_leaves_allocated": 15,
			},
		]
		self.make_records(["employee", "from_date", "to_date"], records)

	def make_leave_applications(self):
		records = [
			{
				"company": "_Test Company",
				"doctype": "Leave Application",
				"employee": "_T-Employee-00001",
				"from_date": "2013-05-01",
				"description": "_Test Reason",
				"leave_type": "_Test Leave Type",
				"posting_date": "2013-01-02",
				"to_date": "2013-05-05",
				"leave_approver": "Administrator",
			},
			{
				"company": "_Test Company",
				"doctype": "Leave Application",
				"employee": "_T-Employee-00002",
				"from_date": "2013-05-01",
				"description": "_Test Reason",
				"leave_type": "_Test Leave Type",
				"posting_date": "2013-01-02",
				"to_date": "2013-05-05",
				"leave_approver": "Administrator",
			},
			{
				"company": "_Test Company",
				"doctype": "Leave Application",
				"employee": "_T-Employee-00001",
				"from_date": "2013-01-15",
				"description": "_Test Reason",
				"leave_type": "_Test Leave Type LWP",
				"posting_date": "2013-01-02",
				"to_date": "2013-01-15",
				"leave_approver": "Administrator",
			},
		]
		self.make_records(["employee", "from_date"], records)

	def make_leave_block_lists(self):
		records = [
			{
				"company": "_Test Company",
				"doctype": "Leave Block List",
				"leave_block_list_allowed": [
					{
						"allow_user": "test1@example.com",
						"doctype": "Leave Block List Allow",
						"parent": "_Test Leave Block List",
						"parentfield": "leave_block_list_allowed",
						"parenttype": "Leave Block List",
					}
				],
				"leave_block_list_dates": [
					{
						"block_date": "2013-01-02",
						"doctype": "Leave Block List Date",
						"parent": "_Test Leave Block List",
						"parentfield": "leave_block_list_dates",
						"parenttype": "Leave Block List",
						"reason": "First work day",
					}
				],
				"leave_block_list_name": "_Test Leave Block List",
				"applies_to_all_departments": 1,
			},
			{
				"company": "_Test Company",
				"doctype": "Leave Block List",
				"leave_type": "Casual Leave",
				"leave_block_list_allowed": [
					{
						"allow_user": "test1@example.com",
						"doctype": "Leave Block List Allow",
						"parent": "_Test Leave Block List Casual Leave 1",
						"parentfield": "leave_block_list_allowed",
						"parenttype": "Leave Block List",
					}
				],
				"leave_block_list_dates": [
					{
						"block_date": "2013-01-16",
						"doctype": "Leave Block List Date",
						"parent": "_Test Leave Block List Casual Leave 1",
						"parentfield": "leave_block_list_dates",
						"parenttype": "Leave Block List",
						"reason": "First work day",
					}
				],
				"leave_block_list_name": "_Test Leave Block List Casual Leave 1",
				"applies_to_all_departments": 1,
			},
			{
				"company": "_Test Company",
				"doctype": "Leave Block List",
				"leave_type": "Casual Leave",
				"leave_block_list_allowed": [],
				"leave_block_list_dates": [
					{
						"block_date": "2013-01-19",
						"doctype": "Leave Block List Date",
						"parent": "_Test Leave Block List Casual Leave 2",
						"parentfield": "leave_block_list_dates",
						"parenttype": "Leave Block List",
						"reason": "First work day",
					}
				],
				"leave_block_list_name": "_Test Leave Block List Casual Leave 2",
				"applies_to_all_departments": 1,
			},
		]
		self.make_records(["leave_block_list_name"], records)

	def update_email_account_settings(self):
		if not frappe.db.exists("Email Account", "Jobs"):
			return
		email_account = frappe.get_doc("Email Account", "Jobs")
		email_account.enable_outgoing = 1
		email_account.default_outgoing = 1
		email_account.save()

	def update_system_settings(self):
		system_settings = frappe.get_doc("System Settings")
		system_settings.country = "India"
		system_settings.currency = system_settings.currency or "INR"
		system_settings.time_zone = system_settings.time_zone or "Asia/Kolkata"
		system_settings.language = system_settings.language or "en"
		system_settings.currency_precision = system_settings.float_precision = 2
		system_settings.save()

	def update_hr_settings(self):
		frappe.db.set_single_value("HR Settings", "leave_approver_mandatory_in_leave_application", 0)

	def make_records(self, key, records):
		doctype = records[0].get("doctype")

		def get_filters(record):
			filters = {}
			for x in key:
				filters[x] = record.get(x)
			return filters

		for x in records:
			filters = get_filters(x)
			if not frappe.db.exists(doctype, filters):
				doc = frappe.get_doc(x).insert()
				if doctype == "Holiday List" and doc.weekly_off:
					doc.get_weekly_off_dates()
					doc.save()


BootStrapTestData()


class HRMSTestSuite(unittest.TestCase):
	"""Base class for qcs_hrms tests: bootstrapped master data, rollback after every test."""

	@classmethod
	def setUpClass(cls):
		cls.globalTestRecords = {}

	def tearDown(self):
		frappe.db.rollback()
		frappe.local.request_cache.clear()
		# tests that switch user without restoring it must not leak the session into the next test
		if frappe.session.user != "Administrator":
			frappe.set_user("Administrator")

	def load_test_records(self, doctype):
		if doctype not in self.globalTestRecords:
			module = frappe.db.get_value("DocType", doctype, "module")
			path = os.path.join(
				frappe.get_module_path(module), "doctype", frappe.scrub(doctype), "test_records.json"
			)
			records = []
			if os.path.exists(path):
				with open(path) as f:
					records = json.load(f)
			self.globalTestRecords[doctype] = records

	@contextmanager
	def set_user(self, user: str):
		old_user = frappe.session.user
		try:
			frappe.set_user(user)
			yield
		finally:
			frappe.set_user(old_user)

	@staticmethod
	@contextmanager
	def change_settings(doctype, settings_dict=None, /, **settings):
		"""Temporarily change values in a settings (single) doctype."""
		if settings_dict is None:
			settings_dict = settings

		doc = frappe.get_doc(doctype)
		previous = {key: doc.get(key) for key in settings_dict}
		doc.update(settings_dict)
		doc.save(ignore_permissions=True)

		try:
			yield
		finally:
			doc = frappe.get_doc(doctype)
			doc.update(previous)
			doc.save(ignore_permissions=True)


def make_user(email: str, role: str = "HR Manager") -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
			}
		).insert()
	frappe.get_doc("User", email).add_roles(role)
	return email


def make_company_restricted_user(email: str, company: str, role: str = "HR Manager") -> str:
	make_user(email, role)
	if not frappe.db.exists("User Permission", {"user": email, "allow": "Company", "for_value": company}):
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": email,
				"allow": "Company",
				"for_value": company,
				"apply_to_all_doctypes": 1,
			}
		).insert()
	return email
