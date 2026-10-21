# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt
import json
from unittest.mock import patch

import frappe
from frappe import _
from frappe.utils import random_string
from frappe.utils.nestedset import get_root_of

from qcs_hrms.organization.doctype.company.company import get_default_company_address
from qcs_hrms.tests.utils import HRMSTestSuite


class TestCompany(HRMSTestSuite):
	def test_basic_tree(self, records=None):
		self.load_test_records("Company")
		min_lft = 1
		max_rgt = frappe.db.sql("select max(rgt) from `tabCompany`")[0][0]

		if not records:
			records = self.globalTestRecords["Company"][2:]

		for company in records:
			lft, rgt, parent_company = frappe.db.get_value(
				"Company", company.get("company_name"), ["lft", "rgt", "parent_company"]
			)

			if parent_company:
				parent_lft, parent_rgt = frappe.db.get_value("Company", parent_company, ["lft", "rgt"])
			else:
				# root
				parent_lft = min_lft - 1
				parent_rgt = max_rgt + 1

			self.assertTrue(lft)
			self.assertTrue(rgt)
			self.assertTrue(lft < rgt)
			self.assertTrue(parent_lft < parent_rgt)
			self.assertTrue(lft > parent_lft)
			self.assertTrue(rgt < parent_rgt)
			self.assertTrue(lft >= min_lft)
			self.assertTrue(rgt <= max_rgt)

	def test_primary_address(self):
		company = "_Test Company"

		secondary = frappe.get_doc(
			{
				"address_title": "Non Primary",
				"doctype": "Address",
				"address_type": "Billing",
				"address_line1": "Something",
				"city": "Mumbai",
				"state": "Maharashtra",
				"country": "India",
				"is_primary_address": 1,
				"pincode": "400098",
				"links": [
					{
						"link_doctype": "Company",
						"link_name": company,
					}
				],
			}
		)
		secondary.insert()
		self.addCleanup(secondary.delete)

		primary = frappe.copy_doc(secondary)
		primary.is_primary_address = 1
		primary.insert()
		self.addCleanup(primary.delete)

		self.assertEqual(get_default_company_address(company), primary.name)

	def get_no_of_children(self, company):
		def get_no_of_children(companies, no_of_children):
			children = []
			for company in companies:
				children += frappe.db.sql_list(
					"""select name from `tabCompany`
				where ifnull(parent_company, '')=%s""",
					company or "",
				)

			if len(children):
				return get_no_of_children(children, no_of_children + len(children))
			else:
				return no_of_children

		return get_no_of_children([company], 0)

	def test_default_departments_ignore_session_translations(self):
		self.assertEqual(get_root_of("Department"), "All Departments")

		translations = {"All Departments": "Alle Abteilungen", "Accounts": "Buchhaltung"}
		with patch("frappe.translate.get_all_translations", return_value=translations):
			company = frappe.new_doc("Company")
			company.company_name = "Dept Translation Test Co"
			company.abbr = "DTTC"
			company.default_currency = "INR"
			company.country = "India"
			company.insert()

		self.assertFalse(frappe.db.exists("Department", "Alle Abteilungen"))
		self.assertEqual(
			frappe.get_all("Department", filters={"parent_department": ("is", "not set")}, pluck="name"),
			["All Departments"],
		)

		departments = frappe.get_all(
			"Department", filters={"company": company.name}, fields=["name", "parent_department"]
		)
		self.assertTrue(departments)
		self.assertEqual({d.parent_department for d in departments}, {"All Departments"})
		self.assertIn("Buchhaltung - DTTC", [d.name for d in departments])

	def test_change_parent_company(self):
		child_company = frappe.get_doc("Company", "_Test Company 5")

		# changing parent of company
		child_company.parent_company = "_Test Company 3"
		child_company.save()
		self.test_basic_tree()

		# move it back
		child_company.parent_company = "_Test Company 4"
		child_company.save()
		self.test_basic_tree()

	def test_fiscal_year_from_start_month(self):
		from qcs_hrms.utils.compat import get_fiscal_year

		company = frappe.get_doc("Company", "_Test Company")
		company.db_set("fiscal_year_start_month", "April")
		self.addCleanup(frappe.clear_cache, doctype="Company")
		frappe.clear_cache(doctype="Company")
		fy = get_fiscal_year("2026-02-10", company=company.name, as_dict=True)
		self.assertEqual(fy.name, "2025-2026")
		self.assertEqual(str(fy.year_start_date), "2025-04-01")
		self.assertEqual(str(fy.year_end_date), "2026-03-31")


def create_company_communication(doctype, docname):
	comm = frappe.get_doc(
		{
			"doctype": "Communication",
			"communication_type": "Communication",
			"content": "Deduplication of Links",
			"communication_medium": "Email",
			"reference_doctype": doctype,
			"reference_name": docname,
		}
	)
	comm.insert()


def create_child_company():
	child_company = frappe.db.exists("Company", "Test Company")
	if not child_company:
		child_company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "Test Company",
				"abbr": "test_company",
				"default_currency": "INR",
			}
		)
		child_company.insert()
	else:
		child_company = frappe.get_doc("Company", child_company)

	return child_company.name


def create_test_lead_in_company(company):
	lead = frappe.db.exists("Lead", "Test Lead in new company")
	if not lead:
		lead = frappe.get_doc(
			{"doctype": "Lead", "lead_name": "Test Lead in new company", "scompany": company}
		)
		lead.insert()
	else:
		lead = frappe.get_doc("Lead", lead)
		lead.company = company
		lead.save()
	return lead.name
