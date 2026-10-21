# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
from frappe.utils import getdate

from qcs_hrms.controllers.employee_boarding_controller import get_activity_todos
from qcs_hrms.tests.utils import HRMSTestSuite


class TestEmployeeSeparation(HRMSTestSuite):
	def test_employee_separation(self):
		separation = create_employee_separation()

		self.assertEqual(separation.docstatus, 1)
		self.assertEqual(separation.boarding_status, "Pending")

		todos = [activity.task for activity in separation.activities]
		self.assertTrue(all(todos))

		for todo in todos:
			todo = frappe.get_doc("ToDo", todo)
			self.assertEqual(todo.reference_type, "Employee Separation")
			todo.status = "Closed"
			todo.save()

		separation.reload()
		self.assertEqual(separation.boarding_status, "Completed")

		separation.cancel()
		for todo in todos:
			self.assertEqual(frappe.db.get_value("ToDo", todo, "status"), "Closed")

	def test_cancelled_todos_complete_activity(self):
		separation = create_employee_separation()
		for todo in get_activity_todos(separation.activities[0]):
			todo = frappe.get_doc("ToDo", todo)
			todo.status = "Cancelled"
			todo.save()

		separation.reload()
		self.assertEqual(separation.boarding_status, "Completed")


def create_employee_separation():
	employee = frappe.db.get_value("Employee", {"status": "Active", "company": "_Test Company"})
	separation = frappe.new_doc("Employee Separation")
	separation.employee = employee
	separation.boarding_begins_on = getdate()
	separation.company = "_Test Company"
	separation.append("activities", {"activity_name": "Deactivate Employee", "role": "HR User"})
	separation.boarding_status = "Pending"
	separation.insert()
	separation.submit()
	return separation
