# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt
import frappe
from frappe import _, scrub, throw
from frappe.model.naming import set_name_by_naming_series
from frappe.permissions import (
	add_user_permission,
	get_doc_permissions,
	remove_user_permission,
)
from frappe.query_builder import Interval
from frappe.query_builder.functions import Count, CurDate, UnixTimestamp
from frappe.utils import add_years, cint, cstr, get_link_to_form, getdate, today, validate_email_address
from frappe.utils.nestedset import NestedSet

from qcs_hrms.utils.compat import delete_events


class EmployeeUserDisabledError(frappe.ValidationError):
	pass


class InactiveEmployeeStatusError(frappe.ValidationError):
	pass


class Employee(NestedSet):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from qcs_hrms.organization.doctype.employee_education.employee_education import EmployeeEducation
		from qcs_hrms.organization.doctype.employee_external_work_history.employee_external_work_history import (
			EmployeeExternalWorkHistory,
		)
		from qcs_hrms.organization.doctype.employee_internal_work_history.employee_internal_work_history import (
			EmployeeInternalWorkHistory,
		)

		attendance_device_id: DF.Data | None
		bank_ac_no: DF.Data | None
		bank_name: DF.Data | None
		bio: DF.TextEditor | None
		blood_group: DF.Literal["", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
		branch: DF.Link | None
		cell_number: DF.Data | None
		company: DF.Link
		company_email: DF.Data | None
		contract_end_date: DF.Date | None
		create_user_automatically: DF.Check
		create_user_permission: DF.Check
		ctc: DF.Currency
		current_accommodation_type: DF.Literal["", "Rented", "Owned"]
		current_address: DF.SmallText | None
		date_of_birth: DF.Date
		date_of_issue: DF.Date | None
		date_of_joining: DF.Date
		date_of_retirement: DF.Date | None
		department: DF.Link | None
		designation: DF.Link | None
		education: DF.Table[EmployeeEducation]
		emergency_phone_number: DF.Data | None
		employee: DF.Data | None
		employee_name: DF.Data | None
		employee_number: DF.Data | None
		encashment_date: DF.Date | None
		external_work_history: DF.Table[EmployeeExternalWorkHistory]
		family_background: DF.SmallText | None
		feedback: DF.SmallText | None
		final_confirmation_date: DF.Date | None
		first_name: DF.Data
		gender: DF.Link
		health_details: DF.SmallText | None
		held_on: DF.Date | None
		holiday_list: DF.Link | None
		iban: DF.Data | None
		image: DF.AttachImage | None
		internal_work_history: DF.Table[EmployeeInternalWorkHistory]
		last_name: DF.Data | None
		leave_encashed: DF.Literal["", "Yes", "No"]
		lft: DF.Int
		marital_status: DF.Literal["", "Single", "Married", "Divorced", "Widowed"]
		middle_name: DF.Data | None
		naming_series: DF.Literal["HR-EMP-"]
		new_workplace: DF.Data | None
		notice_number_of_days: DF.Int
		old_parent: DF.Data | None
		passport_number: DF.Data | None
		permanent_accommodation_type: DF.Literal["", "Rented", "Owned"]
		permanent_address: DF.SmallText | None
		person_to_be_contacted: DF.Data | None
		personal_email: DF.Data | None
		place_of_issue: DF.Data | None
		prefered_contact_email: DF.Literal["", "Company Email", "Personal Email", "User ID"]
		prefered_email: DF.Data | None
		reason_for_leaving: DF.SmallText | None
		relation: DF.Data | None
		relieving_date: DF.Date | None
		reports_to: DF.Link | None
		resignation_letter_date: DF.Date | None
		rgt: DF.Int
		salary_currency: DF.Link | None
		salary_mode: DF.Literal["", "Bank", "Cash", "Cheque"]
		salutation: DF.Link | None
		scheduled_confirmation_date: DF.Date | None
		status: DF.Literal["Active", "Inactive", "Suspended", "Left"]
		unsubscribed: DF.Check
		user_id: DF.Link | None
		valid_upto: DF.Date | None
	# end: auto-generated types

	nsm_parent_field = "reports_to"

	def autoname(self):
		naming_method = frappe.db.get_single_value("HR Settings", "emp_created_by") or "Naming Series"
		if naming_method == "Employee Number":
			if not self.employee_number:
				frappe.throw(_("Please set Employee Number, as per the naming in HR Settings"))
			self.name = self.employee_number
		elif naming_method == "Full Name":
			self.set_employee_name()
			self.name = self.employee_name
		else:
			set_name_by_naming_series(self)

		self.employee = self.name

	def validate(self):
		from qcs_hrms.utils.compat import validate_status

		validate_status(self.status, ["Active", "Inactive", "Suspended", "Left"])

		self.employee = self.name
		self.set_employee_name()
		self.validate_date()
		self.validate_email()
		self.validate_status()
		self.validate_reports_to()
		self.set_preferred_email()
		self.validate_preferred_email()

		if self.user_id:
			self.validate_user_details()
		else:
			existing_user_id = frappe.db.get_value("Employee", self.name, "user_id")
			if existing_user_id:
				user = frappe.get_doc("User", existing_user_id)
				validate_employee_role(user, ignore_emp_check=True)
				user.save(ignore_permissions=True)
				remove_user_permission("Employee", self.name, existing_user_id, ignore_permissions=True)

		validate_onboarding_process(self)

	def after_rename(self, old, new, merge):
		self.db_set("employee", new)

	def set_employee_name(self):
		self.employee_name = " ".join(
			filter(lambda x: x, [self.first_name, self.middle_name, self.last_name])
		)

	def validate_user_details(self):
		if not self.user_id:
			return

		self.validate_for_enabled_user_id()
		self.validate_duplicate_user_id()

	def validate_auto_user_creation(self):
		if self.create_user_automatically and not (
			self.prefered_email or self.company_email or self.personal_email
		):
			frappe.throw(
				_("Company or Personal Email is mandatory when 'Create User Automatically' is enabled"),
				frappe.MandatoryError,
				title=_("Auto User Creation Error"),
			)

	def update_nsm_model(self):
		frappe.utils.nestedset.update_nsm(self)

	def on_update(self):
		self.update_nsm_model()
		frappe.clear_cache()
		if self.user_id:
			self.update_user()
			self.update_user_permissions()
			self.update_user_status()
		self.reset_employee_emails_cache()
		update_approver_role(self)
		publish_update(self)

	def before_insert(self):
		self.validate_auto_user_creation()

	def after_insert(self):
		if self.create_user_automatically and not self.user_id:
			create_user(
				employee=self.name,
				email=self.prefered_email or self.company_email or self.personal_email,
				create_user_permission=self.create_user_permission,
			)

		update_job_applicant_and_offer(self)

	def update_user_permissions(self):
		if not self.has_value_changed("user_id") and not self.has_value_changed("create_user_permission"):
			return

		employee_user_permission_exists = frappe.db.exists(
			"User Permission", {"allow": "Employee", "for_value": self.name, "user": self.user_id}
		)

		if employee_user_permission_exists and not self.create_user_permission:
			remove_user_permission("Employee", self.name, self.user_id, ignore_permissions=True)
			remove_user_permission("Company", self.company, self.user_id, ignore_permissions=True)
		elif not employee_user_permission_exists and self.create_user_permission:
			add_user_permission("Employee", self.name, self.user_id, ignore_permissions=True)
			add_user_permission("Company", self.company, self.user_id, ignore_permissions=True)

	def update_user(self):
		# add employee role if missing
		user = frappe.get_doc("User", self.user_id)
		user.flags.ignore_permissions = True

		if "Employee" not in user.get("roles"):
			user.append_roles("Employee")

		# copy details like Fullname, DOB and Image to User
		if self.employee_name and not (user.first_name and user.last_name):
			employee_name = self.employee_name.split(" ")
			if len(employee_name) >= 3:
				user.last_name = " ".join(employee_name[2:])
				user.middle_name = employee_name[1]
			elif len(employee_name) == 2:
				user.last_name = employee_name[1]

			user.first_name = employee_name[0]

		if self.date_of_birth:
			user.birth_date = self.date_of_birth

		if self.gender:
			user.gender = self.gender

		if self.image:
			if not user.user_image or self.has_value_changed("image"):
				user.user_image = self.image
				try:
					frappe.get_doc(
						{
							"doctype": "File",
							"file_url": self.image,
							"attached_to_doctype": "User",
							"attached_to_name": self.user_id,
						}
					).insert(ignore_if_duplicate=True)
				except frappe.DuplicateEntryError:
					# already exists
					pass

		user.save()

	def validate_date(self):
		if self.date_of_birth and getdate(self.date_of_birth) > getdate(today()):
			throw(_("Date of Birth cannot be greater than today."))

		self.validate_from_to_dates("date_of_birth", "date_of_joining")
		self.validate_from_to_dates("date_of_joining", "date_of_retirement")
		self.validate_from_to_dates("date_of_joining", "relieving_date")
		self.validate_from_to_dates("date_of_joining", "contract_end_date")

	def validate_email(self):
		if self.company_email:
			validate_email_address(self.company_email, True)
		if self.personal_email:
			validate_email_address(self.personal_email, True)

	def set_preferred_email(self):
		preferred_email_field = frappe.scrub(self.prefered_contact_email)
		self.prefered_email = self.get(preferred_email_field) if preferred_email_field else None

	def validate_status(self):
		if self.status == "Left":
			reports_to = frappe.db.get_all(
				"Employee",
				filters={"reports_to": self.name, "status": "Active"},
				fields=["name", "employee_name"],
			)
			if reports_to:
				link_to_employees = [
					frappe.utils.get_link_to_form("Employee", employee.name, label=employee.employee_name)
					for employee in reports_to
				]
				message = _("The following employees are currently still reporting to {0}:").format(
					frappe.bold(self.employee_name)
				)
				message += "<br><br><ul><li>" + "</li><li>".join(link_to_employees)
				message += "</li></ul><br>"
				message += _("Please make sure the employees above report to another Active employee.")
				throw(message, InactiveEmployeeStatusError, _("Cannot Relieve Employee"))
			if not self.relieving_date:
				throw(_("Please enter relieving date."))

	def validate_for_enabled_user_id(self):
		if not frappe.db.exists("User", self.user_id):
			frappe.throw(_("User {0} does not exist").format(self.user_id))

	def update_user_status(self):
		if not self.user_id:
			return

		user = frappe.get_doc("User", self.user_id)
		enabled = user.enabled
		if self.status != "Active" and enabled or self.status == "Active" and enabled == 0:
			user.enabled = not enabled
			# Keep linked User status in sync from the Employee lifecycle and record the audit log.
			user.save(ignore_permissions=True)

	def validate_duplicate_user_id(self):
		Employee = frappe.qb.DocType("Employee")
		employee = (
			frappe.qb.from_(Employee)
			.select(Employee.name)
			.where(
				(Employee.user_id == self.user_id)
				& (Employee.status == "Active")
				& (Employee.name != self.name)
			)
		).run()
		if employee:
			throw(
				_("User {0} is already assigned to Employee {1}").format(self.user_id, employee[0][0]),
				frappe.DuplicateEntryError,
			)

	def validate_reports_to(self):
		if self.reports_to == self.name:
			throw(_("Employee cannot report to himself."))

	def on_trash(self):
		self.update_nsm_model()
		delete_events(self.doctype, self.name)
		update_employee_transfer(self)

	def after_delete(self):
		publish_update(self)

	def validate_preferred_email(self):
		if self.prefered_contact_email and not self.get(scrub(self.prefered_contact_email)):
			frappe.msgprint(_("Please enter {0}").format(self.prefered_contact_email))

	def reset_employee_emails_cache(self):
		prev_doc = self.get_doc_before_save() or {}
		cell_number = cstr(self.get("cell_number"))
		prev_number = cstr(prev_doc.get("cell_number"))
		if cell_number != prev_number or self.get("user_id") != prev_doc.get("user_id"):
			frappe.cache().hdel("employees_with_number", cell_number)
			frappe.cache().hdel("employees_with_number", prev_number)


def validate_employee_role(doc, method=None, ignore_emp_check=False):
	# called via User hook
	if not ignore_emp_check:
		if frappe.db.get_value("Employee", {"user_id": doc.name}):
			return

	user_roles = [d.role for d in doc.get("roles")]
	if "Employee" in user_roles:
		frappe.msgprint(_("User {0}: Removed Employee role as there is no mapped employee.").format(doc.name))
		doc.get("roles").remove(doc.get("roles", {"role": "Employee"})[0])

	if "Employee Self Service" in user_roles:
		frappe.msgprint(
			_("User {0}: Removed Employee Self Service role as there is no mapped employee.").format(doc.name)
		)
		doc.get("roles").remove(doc.get("roles", {"role": "Employee Self Service"})[0])


def get_employee_email(employee_doc):
	return (
		employee_doc.get("user_id") or employee_doc.get("personal_email") or employee_doc.get("company_email")
	)


def get_holiday_list_for_employee(employee, raise_exception=True, as_on=None):
	"""Holiday list in effect for the employee (Holiday List Assignment based).

	Other apps can still override this through the `employee_holiday_list` hook."""
	override = frappe.get_hooks("employee_holiday_list")
	if override:
		return frappe.get_attr(override[-1])(employee, raise_exception, as_on)

	from qcs_hrms.utils.holiday_list import get_holiday_list_for_employee as _get_holiday_list

	if not employee:
		import qcs_hrms

		company = qcs_hrms.get_default_company()
		holiday_list = frappe.get_cached_value("Company", company, "default_holiday_list") if company else None
		if not holiday_list and raise_exception:
			frappe.throw(_("Please set a default Holiday List for Company {0}").format(company))
		return holiday_list

	return _get_holiday_list(employee, raise_exception, as_on)


def is_holiday(employee, date=None, raise_exception=True, only_non_weekly=False, with_description=False):
	"""
	Returns True if given Employee has an holiday on the given date
	        :param employee: Employee `name`
	        :param date: Date to check. Will check for today if None
	        :param raise_exception: Raise an exception if no holiday list found, default is True
	        :param only_non_weekly: Check only non-weekly holidays, default is False
	"""

	holiday_list = get_holiday_list_for_employee(employee, raise_exception)
	if not date:
		date = today()

	if not holiday_list:
		return False

	filters = {"parent": holiday_list, "holiday_date": date}
	if only_non_weekly:
		filters["weekly_off"] = False

	holidays = frappe.get_all("Holiday", fields=["description"], filters=filters, pluck="description")

	if with_description:
		return len(holidays) > 0, holidays

	return len(holidays) > 0


@frappe.whitelist()
def create_user(employee: str, email: str | None = None, create_user_permission: int = 0) -> str:
	emp = frappe.get_doc("Employee", employee)
	emp.check_permission("write")
	if emp.user_id:
		frappe.throw(_("Employee {0} already has a linked user").format(emp.name))

	if not email:
		frappe.throw(_("Email is required to create a user"))

	email = validate_email_address(email, True)
	employee_name = emp.employee_name.split(" ")
	first_name = employee_name[0]
	middle_name = last_name = ""

	if len(employee_name) >= 3:
		last_name = " ".join(employee_name[2:])
		middle_name = employee_name[1]
	elif len(employee_name) == 2:
		last_name = employee_name[1]

	user = frappe.new_doc("User")
	user.update(
		{
			"email": email,
			"enabled": 1,
			"first_name": first_name,
			"middle_name": middle_name,
			"last_name": last_name,
			"gender": emp.gender,
			"birth_date": emp.date_of_birth,
			"phone": emp.cell_number,
			"bio": emp.bio,
		}
	)
	emp.db_set("user_id", email)
	user.append_roles("Employee")
	user.insert()

	emp.user_id = user.name
	emp.create_user_permission = cint(create_user_permission)
	emp.save()

	if cint(create_user_permission):
		add_user_permission("Employee", emp.name, user.name)
		add_user_permission("Company", emp.company, user.name)

	return user.name


def get_all_employee_emails(company):
	"""Returns list of employee emails either based on user_id or company_email"""
	employee_list = frappe.get_all(
		"Employee", fields=["name", "employee_name"], filters={"status": "Active", "company": company}
	)
	employee_emails = []
	for employee in employee_list:
		if not employee:
			continue
		user, company_email, personal_email = frappe.db.get_value(
			"Employee", employee, ["user_id", "company_email", "personal_email"]
		)
		email = user or company_email or personal_email
		if email:
			employee_emails.append(email)
	return employee_emails


def get_employee_emails(employee_list):
	"""Returns list of employee emails either based on user_id or company_email"""
	employee_emails = []
	for employee in employee_list:
		if not employee:
			continue
		user, company_email, personal_email = frappe.db.get_value(
			"Employee", employee, ["user_id", "company_email", "personal_email"]
		)
		email = user or company_email or personal_email
		if email:
			employee_emails.append(email)
	return employee_emails


@frappe.whitelist()
def get_children(doctype, parent=None, company=None, is_root=False, is_tree=False):
	filters = [["status", "=", "Active"]]
	if company and company != "All Companies":
		filters.append(["company", "=", company])

	fields = ["name as value", "employee_name as title"]

	if is_root:
		parent = ""
	if parent and company and parent != company:
		filters.append(["reports_to", "=", parent])
	else:
		filters.append(["reports_to", "=", ""])

	employees = frappe.get_list(doctype, fields=fields, filters=filters, order_by="name")

	for employee in employees:
		is_expandable = frappe.get_all(doctype, filters=[["reports_to", "=", employee.get("value")]])
		employee.expandable = 1 if is_expandable else 0

	return employees


def on_doctype_update():
	frappe.db.add_index("Employee", ["lft", "rgt"])


def has_user_permission_for_employee(user_name, employee_name):
	return frappe.db.exists(
		{
			"doctype": "User Permission",
			"user": user_name,
			"allow": "Employee",
			"for_value": employee_name,
		}
	)


def has_upload_permission(doc, ptype="read", user=None):
	if not user:
		user = frappe.session.user
	if get_doc_permissions(doc, user=user, ptype=ptype).get(ptype):
		return True
	return doc.user_id == user


@frappe.whitelist()
def get_contact_details(employee: str) -> dict:
	"""
	Returns basic contact details for the given employee.

	Email is selected based on the following priority:
	1. Prefered Email
	2. Company Email
	3. Personal Email
	4. User ID
	"""
	if not employee:
		frappe.throw(msg=_("Employee is required"), title=_("Missing Parameter"))

	frappe.has_permission("Employee", "read", employee, throw=True)

	return _get_contact_details(employee)


def _get_contact_details(employee: str) -> dict:
	contact_data = frappe.db.get_value(
		"Employee",
		employee,
		[
			"employee_name",
			"prefered_email",
			"company_email",
			"personal_email",
			"user_id",
			"cell_number",
			"designation",
			"department",
		],
		as_dict=True,
	)

	if not contact_data:
		frappe.throw(msg=_("Employee {0} not found").format(employee), title=_("Not Found"))

	# Email with priority
	employee_email = (
		contact_data.get("prefered_email")
		or contact_data.get("company_email")
		or contact_data.get("personal_email")
		or contact_data.get("user_id")
	)

	return {
		"contact_display": contact_data.get("employee_name"),
		"contact_email": employee_email,
		"contact_mobile": contact_data.get("cell_number"),
		"contact_designation": contact_data.get("designation"),
		"contact_department": contact_data.get("department"),
	}


def validate_onboarding_process(doc, method=None):
	"""Validates Employee Creation for linked Employee Onboarding"""
	job_applicant = doc.get("job_applicant")
	job_offer = doc.get("job_offer")

	filters = {"docstatus": 1, "boarding_status": ("!=", "Completed")}
	if job_offer:
		filters["job_offer"] = job_offer
	elif job_applicant:
		filters["job_applicant"] = job_applicant
	else:
		return

	employee_onboarding = frappe.get_all("Employee Onboarding", filters=filters)
	if employee_onboarding:
		onboarding = frappe.get_doc("Employee Onboarding", employee_onboarding[0].name)
		onboarding.validate_employee_creation()
		onboarding.db_set("employee", doc.name)


def publish_update(doc, method=None):
	import qcs_hrms

	qcs_hrms.refetch_resource("hrms:employee", doc.user_id)


def update_job_applicant_and_offer(doc, method=None):
	"""Updates Job Applicant and Job Offer status as 'Accepted' and submits them"""
	job_applicant = doc.get("job_applicant")
	job_offer = get_linked_job_offer(doc, job_applicant)

	if job_offer and job_offer.status in ("Rejected", "Cancelled"):
		frappe.msgprint(
			_(
				"Linked Job Offer {0} was {1}. Please update the Job Offer status if this hire was intended."
			).format(get_link_to_form("Job Offer", job_offer.name), frappe.bold(_(job_offer.status))),
			title=_("Job Offer Status Not Updated"),
			indicator="orange",
		)
		return

	if job_applicant:
		applicant_status_before_change = frappe.db.get_value("Job Applicant", job_applicant, "status")
		if applicant_status_before_change != "Accepted":
			frappe.db.set_value("Job Applicant", job_applicant, "status", "Accepted")
			frappe.msgprint(
				_("Updated the status of linked Job Applicant {0} to {1}").format(
					get_link_to_form("Job Applicant", job_applicant), frappe.bold(_("Accepted"))
				)
			)

	if not job_offer or job_offer.status == "Accepted":
		return

	job_offer.status = "Accepted"
	job_offer.flags.ignore_mandatory = True
	job_offer.flags.ignore_permissions = True
	job_offer.save()

	msg = _("Updated the status of Job Offer {0} for {1} to {2}").format(
		get_link_to_form("Job Offer", job_offer.name),
		frappe.bold(job_offer.applicant_name),
		frappe.bold(_("Accepted")),
	)
	if job_offer.docstatus == 0:
		msg += "<br>" + _("You may add additional details, if any, and submit the offer.")

	frappe.msgprint(msg)


def get_linked_job_offer(doc, job_applicant: str | None):
	offer_name = doc.get("job_offer")
	if not offer_name and job_applicant:
		offer_name = frappe.db.get_value(
			"Job Offer", {"job_applicant": job_applicant, "docstatus": ["!=", 2]}, "name"
		)
	if not offer_name:
		return None

	job_offer = frappe.get_doc("Job Offer", offer_name)
	return None if job_offer.docstatus == 2 else job_offer


def update_approver_role(doc, method=None):
	"""Adds relevant approver role for the user linked to Employee"""
	if doc.leave_approver:
		user = frappe.get_doc("User", doc.leave_approver)
		user.flags.ignore_permissions = True
		user.add_roles("Leave Approver")

	if doc.expense_approver:
		user = frappe.get_doc("User", doc.expense_approver)
		user.flags.ignore_permissions = True
		user.add_roles("Expense Approver")


def update_approver_user_roles(doc, method=None):
	approver_roles = set()
	if frappe.db.exists("Employee", {"leave_approver": doc.name}):
		approver_roles.add("Leave Approver")

	if frappe.db.exists("Employee", {"expense_approver": doc.name}):
		approver_roles.add("Expense Approver")

	if approver_roles:
		doc.append_roles(*approver_roles)


def update_employee_transfer(doc, method=None):
	"""Unsets Employee ID in Employee Transfer if doc is deleted"""
	if frappe.db.exists("Employee Transfer", {"new_employee_id": doc.name, "docstatus": 1}):
		emp_transfer = frappe.get_doc("Employee Transfer", {"new_employee_id": doc.name, "docstatus": 1})
		emp_transfer.db_set("new_employee_id", "")


@frappe.whitelist()
def get_timeline_data(doctype: str, name: str) -> dict:
	"""Return timeline for attendance"""
	from frappe.desk.notifications import get_open_count

	out = {}

	frappe.has_permission(doctype, "read", name, throw=True)
	frappe.has_permission("Attendance", "read", throw=True)

	open_count = get_open_count(doctype, name)
	out["count"] = open_count["count"]

	Attendance = frappe.qb.DocType("Attendance")

	timeline_data = dict(
		(
			frappe.qb.from_(Attendance)
			.select(
				UnixTimestamp(Attendance.attendance_date),
				Count("*"),
			)
			.where(
				(Attendance.employee == name)
				& (Attendance.docstatus == 1)
				& (Attendance.attendance_date > (CurDate() - Interval(years=1)))
				& (Attendance.status.isin(["Present", "Half Day"]))
			)
			.groupby(Attendance.attendance_date)
		).run()
	)

	out["timeline_data"] = timeline_data
	return out


@frappe.whitelist()
def get_assignable_masters(company: str) -> dict[str, bool]:
	"""Return whether a submitted master exists for each gated Employee assignment action"""
	masters = {
		"Leave Policy": {"docstatus": 1},
		"Salary Structure": {"docstatus": 1, "is_active": "Yes", "company": company},
		"Shift Schedule": {"docstatus": 1},
	}

	return {
		doctype: bool(frappe.get_list(doctype, filters=filters, limit=1, pluck="name"))
		for doctype, filters in masters.items()
	}


@frappe.whitelist()
def get_retirement_date(date_of_birth: str | None = None):
	if date_of_birth:
		try:
			retirement_age = cint(frappe.db.get_single_value("HR Settings", "retirement_age") or 60)
			dt = add_years(getdate(date_of_birth), retirement_age)
			return dt.strftime("%Y-%m-%d")
		except ValueError:
			# invalid date
			return
