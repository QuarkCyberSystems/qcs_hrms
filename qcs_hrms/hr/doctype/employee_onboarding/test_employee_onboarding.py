# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
from frappe.utils import add_days, get_year_ending, get_year_start, getdate

from qcs_hrms.hr.doctype.employee_onboarding.employee_onboarding import (
	IncompleteTaskError,
	make_employee,
)
from qcs_hrms.hr.doctype.job_offer.test_job_offer import create_job_offer
from qcs_hrms.organization.doctype.holiday_list.test_holiday_list import make_holiday_list
from qcs_hrms.tests.utils import HRMSTestSuite


class TestEmployeeOnboarding(HRMSTestSuite):
	def test_employee_onboarding_incomplete_task(self):
		onboarding = create_employee_onboarding()

		# don't allow making employee if onboarding is not complete
		self.assertRaises(IncompleteTaskError, make_employee, onboarding.name)

		# boarding status
		self.assertEqual(onboarding.boarding_status, "Pending")

		# ToDos are created against the onboarding and due on the activity end date
		for activity in onboarding.activities:
			self.assertTrue(activity.task)
			todo = frappe.get_doc("ToDo", activity.task)
			self.assertEqual(todo.reference_type, "Employee Onboarding")
			self.assertEqual(todo.reference_name, onboarding.name)
			self.assertEqual(todo.status, "Open")

		self.assertEqual(
			get_todo_date(onboarding.activities[0].task),
			add_days(onboarding.boarding_begins_on, onboarding.activities[0].duration),
		)
		self.assertEqual(
			get_todo_date(onboarding.activities[1].task),
			add_days(
				onboarding.boarding_begins_on,
				onboarding.activities[1].begin_on + onboarding.activities[1].duration,
			),
		)

		# close the first ToDo
		close_todo(onboarding.activities[0].task)
		onboarding.reload()
		self.assertEqual(onboarding.boarding_status, "In Process")

		# close the remaining ToDos
		close_todo(onboarding.activities[1].task)
		onboarding.reload()
		self.assertEqual(onboarding.boarding_status, "Completed")

		# make employee
		employee = make_employee(onboarding.name)
		employee.first_name = employee.employee_name
		employee.date_of_joining = getdate()
		employee.date_of_birth = "1990-05-08"
		employee.gender = "Female"
		employee.insert()
		self.assertEqual(employee.employee_name, "Test Engineer")

	def test_mark_onboarding_as_completed(self):
		onboarding = create_employee_onboarding()

		# before marking as completed
		self.assertEqual(onboarding.boarding_status, "Pending")
		for status in get_onboarding_todo_statuses(onboarding.name):
			self.assertEqual(status, "Open")

		onboarding.reload()
		onboarding.mark_onboarding_as_completed()

		# after marking as completed
		self.assertEqual(onboarding.boarding_status, "Completed")
		for status in get_onboarding_todo_statuses(onboarding.name):
			self.assertEqual(status, "Closed")

	def test_cancel_onboarding_cancels_todos(self):
		onboarding = create_employee_onboarding()
		onboarding.reload()
		onboarding.cancel()

		for status in get_onboarding_todo_statuses(onboarding.name):
			self.assertEqual(status, "Cancelled")


def get_job_applicant():
	if frappe.db.exists("Job Applicant", "test@engineer.com"):
		return frappe.get_doc("Job Applicant", "test@engineer.com")
	applicant = frappe.new_doc("Job Applicant")
	applicant.applicant_name = "Test Engineer"
	applicant.email_id = "test@engineer.com"
	applicant.designation = "Engineer"
	applicant.status = "Open"
	applicant.cover_letter = "I am a great Engineer."
	applicant.insert()
	return applicant


def get_job_offer(applicant_name):
	job_offer = frappe.db.exists("Job Offer", {"job_applicant": applicant_name})
	if job_offer:
		return frappe.get_doc("Job Offer", job_offer)

	job_offer = create_job_offer(job_applicant=applicant_name, company="_Test Company")
	job_offer.submit()
	return job_offer


def create_employee_onboarding():
	applicant = get_job_applicant()
	job_offer = get_job_offer(applicant.name)

	holiday_list = make_holiday_list(
		"_Test Employee Boarding",
		from_date=get_year_start(getdate()),
		to_date=get_year_ending(getdate()),
		holiday_dates=[],
	)

	onboarding = frappe.new_doc("Employee Onboarding")
	onboarding.job_applicant = applicant.name
	onboarding.job_offer = job_offer.name
	onboarding.date_of_joining = onboarding.boarding_begins_on = getdate()
	onboarding.company = "_Test Company"
	onboarding.holiday_list = holiday_list.name
	onboarding.designation = "Engineer"
	onboarding.append(
		"activities",
		{
			"activity_name": "Assign ID Card",
			"role": "HR User",
			"required_for_employee_creation": 1,
			"begin_on": 0,
			"duration": 1,
		},
	)
	onboarding.append(
		"activities",
		{"activity_name": "Assign a laptop", "role": "HR User", "begin_on": 1, "duration": 1},
	)
	onboarding.status = "Pending"
	onboarding.insert()
	onboarding.submit()

	return onboarding


def get_todo_date(todo: str):
	return getdate(frappe.db.get_value("ToDo", todo, "date"))


def close_todo(todo: str):
	todo = frappe.get_doc("ToDo", todo)
	todo.status = "Closed"
	todo.save()


def get_onboarding_todo_statuses(onboarding: str) -> list[str]:
	return frappe.get_all(
		"ToDo",
		filters={"reference_type": "Employee Onboarding", "reference_name": onboarding},
		pluck="status",
	)
