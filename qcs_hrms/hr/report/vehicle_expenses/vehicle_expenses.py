# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import add_days, flt, formatdate, get_first_day, get_last_day, getdate

from qcs_hrms.utils.compat import get_fiscal_year, get_fiscal_year_dates


def execute(filters=None):
	filters = frappe._dict(filters or {})

	columns = get_columns()
	data = get_vehicle_log_data(filters)
	chart = get_chart_data(data, filters)

	return columns, data, None, chart


def get_columns():
	return [
		{
			"fieldname": "vehicle",
			"fieldtype": "Link",
			"label": _("Vehicle"),
			"options": "Vehicle",
			"width": 150,
		},
		{"fieldname": "make", "fieldtype": "Data", "label": _("Make"), "width": 100},
		{"fieldname": "model", "fieldtype": "Data", "label": _("Model"), "width": 80},
		{"fieldname": "location", "fieldtype": "Data", "label": _("Location"), "width": 100},
		{
			"fieldname": "log_name",
			"fieldtype": "Link",
			"label": _("Vehicle Log"),
			"options": "Vehicle Log",
			"width": 100,
		},
		{"fieldname": "odometer", "fieldtype": "Int", "label": _("Odometer Value"), "width": 120},
		{"fieldname": "date", "fieldtype": "Date", "label": _("Date"), "width": 100},
		{"fieldname": "fuel_qty", "fieldtype": "Float", "label": _("Fuel Qty"), "width": 80},
		{"fieldname": "fuel_price", "fieldtype": "Float", "label": _("Fuel Price"), "width": 100},
		{"fieldname": "fuel_expense", "fieldtype": "Currency", "label": _("Fuel Expense"), "width": 150},
		{
			"fieldname": "service_expense",
			"fieldtype": "Currency",
			"label": _("Service Expense"),
			"width": 150,
		},
		{
			"fieldname": "employee",
			"fieldtype": "Link",
			"label": _("Employee"),
			"options": "Employee",
			"width": 150,
		},
	]


def get_vehicle_log_data(filters):
	start_date, end_date = get_period_dates(filters)

	vhcl = frappe.qb.DocType("Vehicle")
	log = frappe.qb.DocType("Vehicle Log")

	query = (
		frappe.qb.from_(vhcl)
		.from_(log)
		.select(
			vhcl.license_plate.as_("vehicle"),
			vhcl.make,
			vhcl.model,
			vhcl.location,
			log.name.as_("log_name"),
			log.odometer,
			log.date,
			log.employee,
			log.fuel_qty,
			log.price.as_("fuel_price"),
			(log.fuel_qty * log.price).as_("fuel_expense"),
		)
		.where(vhcl.license_plate == log.license_plate)
		.where(log.docstatus == 1)
		.where(log.date[start_date:end_date])
		.orderby(log.date)
	)

	if filters.employee:
		query = query.where(log.employee == filters.employee)

	if filters.vehicle:
		query = query.where(vhcl.license_plate == filters.vehicle)

	data = query.run(as_dict=True)

	for row in data:
		row["service_expense"] = get_service_expense(row.log_name)

	return data


def get_period_dates(filters):
	if filters.filter_based_on == "Fiscal Year" or not (filters.from_date and filters.to_date):
		fiscal_year = filters.fiscal_year or get_fiscal_year(getdate(), company=filters.company)[0]
		return get_fiscal_year_dates(fiscal_year, filters.company)
	return getdate(filters.from_date), getdate(filters.to_date)


def get_period_list(from_date, to_date) -> list[frappe._dict]:
	"""Monthly periods covering from_date..to_date (monthly period helper)."""
	periods = []
	start = get_first_day(from_date)
	while start <= getdate(to_date):
		end = get_last_day(start)
		periods.append(
			frappe._dict(
				key=start.strftime("%b_%Y").lower(),
				label=formatdate(start, "MMM YYYY"),
				from_date=max(start, getdate(from_date)),
				to_date=min(end, getdate(to_date)),
			)
		)
		start = add_days(end, 1)
	return periods


def get_service_expense(logname):
	log = frappe.qb.DocType("Vehicle Log")
	service = frappe.qb.DocType("Vehicle Service")

	expense_amount = (
		frappe.qb.from_(log)
		.inner_join(service)
		.on(service.parent == log.name)
		.select(Sum(service.expense_amount))
		.where((log.name == logname) & (log.docstatus == 1))
	).run()

	return flt(expense_amount[0][0]) if expense_amount else 0.0


def get_chart_data(data, filters):
	period_list = get_period_list(*get_period_dates(filters))

	fuel_data, service_data = [], []

	for period in period_list:
		total_fuel_exp = 0
		total_service_exp = 0

		for row in data:
			if row.date <= period.to_date and row.date >= period.from_date:
				total_fuel_exp += flt(row.fuel_expense)
				total_service_exp += flt(row.service_expense)

		fuel_data.append([period.key, total_fuel_exp])
		service_data.append([period.key, total_service_exp])

	labels = [period.label for period in period_list]
	fuel_exp_data = [row[1] for row in fuel_data]
	service_exp_data = [row[1] for row in service_data]

	datasets = []
	if fuel_exp_data:
		datasets.append({"name": _("Fuel Expenses"), "values": fuel_exp_data})

	if service_exp_data:
		datasets.append({"name": _("Service Expenses"), "values": service_exp_data})

	chart = {
		"data": {"labels": labels, "datasets": datasets},
		"type": "line",
		"fieldtype": "Currency",
	}

	return chart
