# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.desk.form.assign_to import notify_assignment
from frappe.model.document import Document
from frappe.utils import add_days, flt, formatdate, getdate, nowdate, unique

from qcs_hrms.organization.doctype.employee.employee import get_holiday_list_for_employee
from qcs_hrms.organization.doctype.holiday_list.holiday_list import is_holiday

BOARDING_DOCTYPES = ("Employee Onboarding", "Employee Separation")
DONE_STATUSES = ("Closed", "Cancelled")


class EmployeeBoardingController(Document):
	"""
	Create a ToDo per activity (one per assigned user) for the boarding process.
	Activities are assigned to the user / role members as per the onboarding/separation template.
	The boarding status is derived from the status of these ToDos.
	"""

	def validate(self):
		# remove the ToDo links copied over from the cancelled document
		if self.amended_from:
			for activity in self.activities:
				activity.task = ""
				activity.todos = ""

	def on_submit(self):
		self.db_set("boarding_status", "Pending")
		self.reload()
		self.create_task_and_notify_user()

	def create_task_and_notify_user(self):
		"""Create ToDos for activities that do not have one yet and notify the assignees"""
		holiday_list = None

		for activity in self.activities:
			if activity.task:
				continue

			if holiday_list is None:
				holiday_list = self.get_holiday_list()

			start_date, end_date = self.get_task_dates(activity, holiday_list)
			description = self.get_activity_description(activity, start_date, end_date)
			users = self.get_activity_users(activity) or [None]

			todos = []
			for user in users:
				todo = frappe.get_doc(
					{
						"doctype": "ToDo",
						"allocated_to": user,
						"role": activity.role,
						"reference_type": self.doctype,
						"reference_name": self.name,
						"description": description,
						"priority": "Medium",
						"status": "Open",
						"date": end_date or start_date or nowdate(),
						"assigned_by": frappe.session.user,
					}
				)
				todo.flags.ignore_boarding_sync = True
				todo.insert(ignore_permissions=True)
				todos.append(todo.name)

				if user:
					self.share_with_assignee(user)
					notify_assignment(
						todo.assigned_by,
						user,
						self.doctype,
						self.name,
						action="ASSIGN",
						description=description,
					)

			activity.db_set({"task": todos[0], "todos": "\n".join(todos)})

	def get_activity_users(self, activity) -> list[str]:
		users = [activity.user] if activity.user else []
		if activity.role:
			has_role = frappe.qb.DocType("Has Role")
			user = frappe.qb.DocType("User")
			user_list = (
				frappe.qb.from_(has_role)
				.left_join(user)
				.on(has_role.parent == user.name)
				.select(has_role.parent)
				.distinct()
				.where(
					(has_role.parenttype == "User") & (user.enabled == 1) & (has_role.role == activity.role)
				)
				.run(pluck="parent")
			)
			users = unique(users + user_list)

		if "Administrator" in users and len(users) > 1:
			users.remove("Administrator")

		return users

	def get_activity_description(self, activity, start_date, end_date) -> str:
		subject = frappe.bold(f"{activity.activity_name} : {self.employee_name or self.name}")
		lines = [subject]
		if start_date:
			lines.append(_("Start Date: {0}").format(formatdate(start_date)))
		if end_date:
			lines.append(_("Due Date: {0}").format(formatdate(end_date)))
		if activity.description:
			lines.append(activity.description)
		return "<br>".join(lines)

	def share_with_assignee(self, user):
		"""Give read access to the boarding document if the assignee can not access it"""
		if frappe.has_permission(doc=self, user=user):
			return
		if frappe.get_system_settings("disable_document_sharing"):
			return
		frappe.share.add_docshare(
			self.doctype, self.name, user, read=1, flags={"ignore_share_permission": True}
		)

	def get_holiday_list(self):
		if self.doctype == "Employee Separation":
			return get_holiday_list_for_employee(self.employee, as_on=self.boarding_begins_on)
		else:
			if self.employee:
				return get_holiday_list_for_employee(self.employee, as_on=self.boarding_begins_on)
			else:
				if not self.holiday_list:
					frappe.throw(_("Please set the Holiday List."), frappe.MandatoryError)
				else:
					return self.holiday_list

	def get_task_dates(self, activity, holiday_list):
		start_date = end_date = None

		if activity.begin_on is not None:
			start_date = add_days(self.boarding_begins_on, activity.begin_on)
			start_date = self.update_if_holiday(start_date, holiday_list)

			if activity.duration is not None:
				end_date = add_days(self.boarding_begins_on, activity.begin_on + activity.duration)
				end_date = self.update_if_holiday(end_date, holiday_list)

		return [start_date, end_date]

	def update_if_holiday(self, date, holiday_list):
		while is_holiday(holiday_list, date):
			date = add_days(date, 1)
		return date

	def set_activity_todo_status(self, status: str, from_statuses: tuple = ("Open",)):
		"""Set the status of all ToDos linked to this document's activities"""
		for todo_name in get_todo_names(self.activities):
			todo = frappe.get_doc("ToDo", todo_name)
			if todo.status not in from_statuses:
				continue
			todo.status = status
			todo.flags.ignore_boarding_sync = True
			todo.save(ignore_permissions=True)

	def on_cancel(self):
		self.set_activity_todo_status("Cancelled")
		frappe.msgprint(_("Linked ToDos cancelled."), alert=True, indicator="blue")


def get_activity_todos(activity) -> list[str]:
	todos = [t.strip() for t in (activity.get("todos") or "").split("\n") if t.strip()]
	if activity.get("task") and activity.task not in todos:
		todos.insert(0, activity.task)
	return todos


def get_todo_names(activities) -> list[str]:
	names = []
	for activity in activities:
		names.extend(get_activity_todos(activity))
	return [name for name in unique(names) if frappe.db.exists("ToDo", name)]


def is_activity_done(statuses: list[str]) -> bool:
	"""An activity is done when any assignee closed it, or every assignment was cancelled"""
	if not statuses:
		return False
	return "Closed" in statuses or all(status == "Cancelled" for status in statuses)


def get_activity_status_map(activities) -> dict[str, bool]:
	"""Return {activity row name: done} for activities that have ToDos"""
	todo_status = dict(
		frappe.get_all(
			"ToDo",
			filters={"name": ("in", [t for a in activities for t in get_activity_todos(a)] or [""])},
			fields=["name", "status"],
			as_list=True,
		)
	)
	result = {}
	for activity in activities:
		todos = get_activity_todos(activity)
		if not todos:
			continue
		result[activity.name] = is_activity_done([todo_status[t] for t in todos if t in todo_status])
	return result


def get_boarding_activities(doctype: str, name: str) -> list:
	return frappe.get_all(
		"Employee Boarding Activity",
		filters={"parenttype": doctype, "parent": name, "parentfield": "activities"},
		fields=["name", "task", "todos", "task_weight", "required_for_employee_creation"],
		order_by="idx",
	)


def update_employee_boarding_status(doctype: str, name: str):
	"""Compute the boarding status from the ToDos of the boarding activities"""
	if frappe.db.get_value(doctype, name, "docstatus") != 1:
		return

	activities = get_boarding_activities(doctype, name)
	status_map = get_activity_status_map(activities)
	if not status_map:
		return

	tracked = [a for a in activities if a.name in status_map]
	total_weight = sum(flt(a.task_weight) for a in tracked)
	if total_weight:
		done = sum(flt(a.task_weight) for a in tracked if status_map[a.name])
		percent = flt(done / total_weight * 100, 2)
	else:
		percent = flt(len([a for a in tracked if status_map[a.name]]) / len(tracked) * 100, 2)

	status = "Pending"
	if 0.0 < percent < 100.0:
		status = "In Process"
	elif percent == 100.0:
		status = "Completed"

	frappe.db.set_value(doctype, name, "boarding_status", status)


def update_boarding_status_from_todo(todo, method=None):
	"""ToDo on_update: sync sibling assignments of the activity and recompute the boarding status"""
	if todo.reference_type not in BOARDING_DOCTYPES or not todo.reference_name:
		return
	if todo.flags.ignore_boarding_sync:
		return

	activities = get_boarding_activities(todo.reference_type, todo.reference_name)
	activity = next((a for a in activities if todo.name in get_activity_todos(a)), None)
	if not activity:
		return

	# closing / reopening one assignment of an activity applies to all its assignees
	if todo.has_value_changed("status") and todo.status in ("Open", "Closed"):
		from_status = "Closed" if todo.status == "Open" else "Open"
		for sibling in get_activity_todos(activity):
			if sibling == todo.name:
				continue
			if frappe.db.get_value("ToDo", sibling, "status") != from_status:
				continue
			sibling_doc = frappe.get_doc("ToDo", sibling)
			sibling_doc.status = todo.status
			sibling_doc.flags.ignore_boarding_sync = True
			sibling_doc.save(ignore_permissions=True)

	update_employee_boarding_status(todo.reference_type, todo.reference_name)


@frappe.whitelist()
def get_onboarding_details(parent: str, parenttype: str):
	frappe.has_permission(parenttype, "read", parent, throw=True)
	return frappe.get_all(
		"Employee Boarding Activity",
		fields=[
			"activity_name",
			"role",
			"user",
			"required_for_employee_creation",
			"description",
			"task_weight",
			"begin_on",
			"duration",
		],
		filters={"parent": parent, "parenttype": parenttype},
		order_by="idx",
	)
