# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc

from qcs_hrms.controllers.employee_boarding_controller import (
	EmployeeBoardingController,
	get_activity_status_map,
)


class IncompleteTaskError(frappe.ValidationError):
	pass


class EmployeeOnboarding(EmployeeBoardingController):
	def validate(self):
		super().validate()
		self.set_employee()
		self.validate_duplicate_employee_onboarding()

	def set_employee(self):
		if not self.employee:
			self.employee = frappe.db.get_value("Employee", {"job_offer": self.job_offer}, "name")

	def validate_duplicate_employee_onboarding(self):
		emp_onboarding = frappe.db.exists(
			"Employee Onboarding", {"job_offer": self.job_offer, "docstatus": ("!=", 2)}
		)
		if emp_onboarding and emp_onboarding != self.name:
			frappe.throw(
				_("Employee Onboarding: {0} already exists for Job Offer: {1}").format(
					frappe.bold(emp_onboarding), frappe.bold(self.job_offer)
				)
			)

	def validate_employee_creation(self):
		if self.docstatus != 1:
			frappe.throw(_("Submit this to create the Employee record"))
		else:
			required = [a for a in self.activities if a.required_for_employee_creation]
			if not required:
				return

			status_map = get_activity_status_map(required)
			if not all(status_map.get(activity.name) for activity in required):
				frappe.throw(
					_("All the mandatory tasks for employee creation are not completed yet."),
					IncompleteTaskError,
				)

	def on_submit(self):
		super().on_submit()

	def on_update_after_submit(self):
		self.create_task_and_notify_user()

	def on_cancel(self):
		super().on_cancel()

	@frappe.whitelist(methods=["POST"])
	def mark_onboarding_as_completed(self):
		self.check_permission("write")
		self.set_activity_todo_status("Closed")
		self.boarding_status = "Completed"
		self.save()


@frappe.whitelist()
def make_employee(source_name: str, target_doc: str | Document | None = None) -> Document:
	doc = frappe.get_doc("Employee Onboarding", source_name)
	doc.validate_employee_creation()

	def set_missing_values(source, target):
		target.personal_email = frappe.db.get_value("Job Offer", source.job_offer, "applicant_email")
		target.job_offer = source.job_offer
		target.status = "Active"

	doc = get_mapped_doc(
		"Employee Onboarding",
		source_name,
		{
			"Employee Onboarding": {
				"doctype": "Employee",
				"field_map": {
					"first_name": "employee_name",
					"employee_grade": "grade",
				},
			}
		},
		target_doc,
		set_missing_values,
	)
	return doc
