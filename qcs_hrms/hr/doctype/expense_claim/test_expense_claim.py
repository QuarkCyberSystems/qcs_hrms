# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors and Contributors
# See license.txt

import frappe
from frappe.utils import flt, nowdate, today

from qcs_hrms import get_company_currency
from qcs_hrms.hr.doctype.expense_claim.expense_claim import (
	MismatchError,
	get_advances,
	get_outstanding_amount_for_claim,
	make_payment,
)
from qcs_hrms.ledger.doctype.hr_ledger_account.test_hr_ledger_account import create_ledger_account
from qcs_hrms.ledger.setup import create_default_accounts
from qcs_hrms.organization.doctype.employee.test_employee import make_employee
from qcs_hrms.tests.utils import HRMSTestSuite, make_user
from qcs_hrms.utils.compat import get_exchange_rate

company_name = "_Test Company 3"


class TestExpenseClaim(HRMSTestSuite):
	def setUp(self):
		for company in ("_Test Company", company_name):
			create_default_accounts(company)
		frappe.set_user("Administrator")

	def test_expense_claim_status_as_payment(self):
		payable_account = get_payable_account(company_name)
		expense_claim = make_expense_claim(
			payable_account, 300, 200, company_name, get_expense_account(company_name)
		)
		self.assertEqual(expense_claim.status, "Unpaid")

		payment = make_claim_payment_entry(expense_claim, 200)
		self.assertEqual(payment.doctype, "HR Payment")

		expense_claim.load_from_db()
		self.assertEqual(expense_claim.status, "Paid")
		self.assertEqual(expense_claim.total_amount_reimbursed, 200)

		payment.cancel()
		expense_claim.load_from_db()
		self.assertEqual(expense_claim.status, "Unpaid")
		self.assertEqual(expense_claim.total_amount_reimbursed, 0)

		# expense claim without any sanctioned amount should not have status as Paid
		claim = make_expense_claim(payable_account, 1000, 0, "_Test Company", get_expense_account())
		self.assertEqual(claim.total_sanctioned_amount, 0)
		self.assertEqual(claim.status, "Submitted")

		# no ledger entries created
		entries = frappe.get_all("HR Ledger Entry", {"voucher_type": "Expense Claim", "voucher_no": claim.name})
		self.assertEqual(len(entries), 0)

	def test_payment_cannot_exceed_outstanding(self):
		payable_account = get_payable_account(company_name)
		expense_claim = make_expense_claim(
			payable_account, 300, 200, company_name, get_expense_account(company_name)
		)
		payment = make_payment(expense_claim.name)
		payment.references[0].allocated_amount = 250
		payment.paid_amount = 250
		self.assertRaises(frappe.ValidationError, payment.insert)

	def test_expense_claim_ledger_entries(self):
		payable_account = get_payable_account(company_name)
		taxes = generate_taxes()
		expense_claim = make_expense_claim(
			payable_account,
			300,
			200,
			company_name,
			get_expense_account(company_name),
			do_not_submit=True,
			taxes=taxes,
		)
		expense_claim.submit()

		entries = frappe.get_all(
			"HR Ledger Entry",
			filters={"voucher_type": "Expense Claim", "voucher_no": expense_claim.name, "is_cancelled": 0},
			fields=["account", "debit", "credit", "party", "against_voucher", "sync_status"],
			order_by="account",
		)
		self.assertTrue(entries)

		expected_values = {
			taxes["taxes"][0]["account_head"]: [18.0, 0.0],
			payable_account: [0.0, 218.0],
			get_expense_account(company_name): [200.0, 0.0],
		}
		self.assertEqual(len(entries), len(expected_values))
		for entry in entries:
			self.assertEqual(expected_values[entry.account], [entry.debit, entry.credit])
			self.assertEqual(entry.sync_status, "Pending")

		payable = next(e for e in entries if e.account == payable_account)
		self.assertEqual(payable.party, expense_claim.employee)
		self.assertEqual(payable.against_voucher, expense_claim.name)

		# cancellation reverses the entries
		expense_claim.cancel()
		self.assertFalse(
			frappe.get_all(
				"HR Ledger Entry",
				filters={"voucher_type": "Expense Claim", "voucher_no": expense_claim.name, "is_cancelled": 0},
			)
		)

	def test_paid_claim_posts_payment(self):
		payable_account = get_payable_account(company_name)
		bank = create_default_accounts(company_name)["Cash"]
		expense_claim = make_expense_claim(
			payable_account,
			300,
			300,
			company_name,
			get_expense_account(company_name),
			args={"is_paid": 1, "bank_or_cash_account": bank},
		)
		self.assertEqual(expense_claim.status, "Paid")

		bank_entry = frappe.db.get_value(
			"HR Ledger Entry",
			{"voucher_type": "Expense Claim", "voucher_no": expense_claim.name, "account": bank},
			["debit", "credit"],
			as_dict=True,
		)
		self.assertEqual(bank_entry.credit, 300)

	def test_repost_on_cost_center_change(self):
		payable_account = get_payable_account(company_name)
		taxes = generate_taxes(rate=10)
		expense_claim = make_expense_claim(
			payable_account, 100, 100, company_name, get_expense_account(company_name), taxes=taxes
		)

		expense_claim.reload()
		expense_claim.expenses[0].cost_center = "Projects"
		expense_claim.save()

		active = frappe.get_all(
			"HR Ledger Entry",
			filters={"voucher_type": "Expense Claim", "voucher_no": expense_claim.name, "is_cancelled": 0},
			fields=["account", "cost_center", "debit", "credit"],
		)
		self.assertEqual(sum(e.debit for e in active), 110)
		self.assertEqual(sum(e.credit for e in active), 110)
		expense_line = next(e for e in active if e.account == get_expense_account(company_name))
		self.assertEqual(expense_line.cost_center, "Projects")

	def test_other_employee_advances_link_with_claim(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import make_employee_advance

		payable_account = get_payable_account("_Test Company")

		employee = make_employee("test_employee@employee.advance", "_Test Company")
		advance = make_employee_advance(employee)

		employee_with_no_advance = make_employee("test_employee@not-employee.advance", "_Test Company")
		claim_with_no_advance = make_expense_claim(
			payable_account,
			1000,
			1000,
			"_Test Company",
			get_expense_account(),
			do_not_submit=True,
			employee=employee_with_no_advance,
		)
		claim_with_no_advance.save()

		claim_with_no_advance.append(
			"advances",
			{
				"employee_advance": advance.name,
			},
		)
		self.assertRaises(frappe.ValidationError, claim_with_no_advance.save)


	def test_expense_claim_against_fully_paid_advances(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			get_advances_for_claim,
			make_employee_advance,
			make_payment_entry,
		)

		frappe.db.delete("Employee Advance")

		payable_account = get_payable_account("_Test Company")
		claim = make_expense_claim(
			payable_account, 1000, 1000, "_Test Company", get_expense_account(), do_not_submit=True
		)

		advance = make_employee_advance(claim.employee)
		make_payment_entry(advance)

		# claim for already paid out advances
		claim = get_advances_for_claim(claim, advance.name)
		claim.save()
		claim.submit()

		self.assertEqual(claim.grand_total, 0)
		self.assertEqual(claim.status, "Paid")
		advance_row = claim.advances[0]
		self.assertEqual(advance_row.employee_advance, advance.name)
		self.assertEqual(advance_row.advance_paid, 1000)
		self.assertEqual(advance_row.unclaimed_amount, 1000)
		self.assertEqual(advance_row.allocated_amount, 1000)


	def test_validate_advances_blocks_incompletely_linked_advance(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			make_employee_advance,
			make_payment_entry,
		)

		payable_account = get_payable_account("_Test Company")
		claim = make_expense_claim(
			payable_account, 1000, 1000, "_Test Company", get_expense_account(), do_not_submit=True
		)

		advance = make_employee_advance(claim.employee)
		make_payment_entry(advance)

		# advance_account is set by get_advances;
		# a row added without them cannot book its accounting entry
		claim.append(
			"advances",
			{"employee_advance": advance.name, "allocated_amount": 1000, "unclaimed_amount": 1000},
		)

		self.assertRaisesRegex(frappe.ValidationError, "required against advance", claim.save)


	def test_advance_amount_allocation_against_claim_with_taxes(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			get_advances_for_claim,
			make_employee_advance,
			make_payment_entry,
		)

		frappe.db.delete("Employee Advance")

		payable_account = get_payable_account("_Test Company")
		taxes = generate_taxes("_Test Company")
		claim = make_expense_claim(
			payable_account,
			700,
			700,
			"_Test Company",
			get_expense_account(),
			do_not_submit=True,
			taxes=taxes,
		)
		claim.save()

		advance = make_employee_advance(claim.employee)
		make_payment_entry(advance)

		# claim for already paid out advances
		claim = get_advances_for_claim(claim, advance.name, 763)
		claim.save()
		claim.submit()

		self.assertEqual(claim.grand_total, 0)
		self.assertEqual(claim.status, "Paid")


	def test_expense_claim_partially_paid_via_advance(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			make_employee_advance,
			make_payment_entry,
		)
		from qcs_hrms.hr.doctype.expense_claim.expense_claim import get_expense_claim

		frappe.db.delete("Employee Advance")

		employee = make_employee("test_partial_advance_claim@expenseclaim.com", "_Test Company")
		advance = make_employee_advance(employee, {"advance_amount": 500})
		make_payment_entry(advance)

		currency, cost_center = frappe.db.get_value(
			"Company", "_Test Company", ["default_currency", "cost_center"]
		)
		claim = get_expense_claim(advance.name)  # function call to create claim from employee advance form
		claim.update(
			{
				"payable_account": get_payable_account("_Test Company"),
				"currency": currency,
				"exchange_rate": 1,
				"approval_status": "Approved",
			}
		)
		claim.append(
			"expenses",
			{
				"expense_type": "Travel",
				"default_account": get_expense_account(),
				"amount": 1000,
				"sanctioned_amount": 1000,
				"cost_center": cost_center,
			},
		)

		# assert is_paid to be checked true as claim is done via advance actions button
		self.assertTrue(claim.is_paid)
		claim.save()
		claim.submit()

		# assert is_paid to be checked false as claim amount is greater than advance
		self.assertFalse(claim.is_paid)
		self.assertEqual(claim.grand_total, 500)
		self.assertEqual(claim.status, "Unpaid")

		# reimburse remaning amount
		make_claim_payment_entry(claim, 500)
		claim.reload()

		self.assertEqual(claim.total_amount_reimbursed, 500)
		self.assertEqual(claim.status, "Paid")
		self.assertEqual(claim.total_claimed_amount, 1000)
		advance_row = claim.advances[0]
		self.assertEqual(advance_row.advance_paid, 500)
		self.assertEqual(advance_row.unclaimed_amount, 500)
		self.assertEqual(advance_row.allocated_amount, 500)


	def test_expense_claim_with_deducted_returned_advance(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			create_payroll_for_advance_return,
			get_advances_for_claim,
			make_employee_advance,
			make_payment_entry,
		)
		from qcs_hrms.hr.doctype.expense_claim.expense_claim import get_allocation_amount

		company_doc = frappe.get_doc("Company", "_Test Company")
		# create employee and employee advance
		employee_name = make_employee("_T@employee.advance", company_doc.name)
		advance = make_employee_advance(employee_name, {"repay_unclaimed_amount_from_salary": 1})
		make_payment_entry(advance)
		advance.reload()

		create_payroll_for_advance_return(employee_name, company_doc, advance, return_amount=400)
		advance.reload()
		self.assertEqual(advance.return_amount, 400)

		# create an expense claim
		payable_account = get_payable_account(company_doc.name)
		claim = make_expense_claim(
			payable_account,
			200,
			200,
			company_doc.name,
			get_expense_account(),
			do_not_submit=True,
			employee=employee_name,
		)

		# link advance to the claim
		claim = get_advances_for_claim(claim, advance.name, amount=200)
		claim.save()
		claim.submit()

		# verify the allocation amount
		advance = claim.advances[0]
		self.assertEqual(
			get_allocation_amount(
				unclaimed_amount=advance.unclaimed_amount, return_amount=advance.return_amount
			),
			600,
		)
		advance_row = claim.advances[0]
		self.assertEqual(advance_row.advance_paid, 1000)
		self.assertEqual(advance_row.return_amount, 400)
		self.assertEqual(advance_row.allocated_amount, 200)


	def test_rejected_expense_claim(self):
		payable_account = get_payable_account(company_name)
		expense_claim = make_expense_claim(
			payable_account, 300, 200, company_name, get_expense_account(company_name), approval_status="Rejected"
		)
		expense_claim.submit()

		self.assertEqual(expense_claim.status, "Rejected")
		self.assertEqual(expense_claim.total_sanctioned_amount, 0.0)

		ledger_entries = frappe.get_all(
			"HR Ledger Entry", {"voucher_type": "Expense Claim", "voucher_no": expense_claim.name}
		)
		self.assertEqual(len(ledger_entries), 0)


	def test_expense_approver_perms(self):
		user = "test_approver_perm_emp@example.com"
		make_employee(user, "_Test Company")

		# check doc shared
		payable_account = get_payable_account("_Test Company")
		expense_claim = make_expense_claim(
			payable_account, 300, 200, "_Test Company", get_expense_account(), do_not_submit=True
		)
		expense_claim.expense_approver = user
		expense_claim.save()
		self.assertTrue(expense_claim.name in frappe.share.get_shared("Expense Claim", user))

		# check shared doc revoked
		expense_claim.reload()
		expense_claim.expense_approver = "test@example.com"
		expense_claim.save()
		self.assertTrue(expense_claim.name not in frappe.share.get_shared("Expense Claim", user))

		expense_claim.reload()
		expense_claim.expense_approver = user
		expense_claim.save()

		frappe.set_user(user)
		expense_claim.reload()
		expense_claim.status = "Approved"
		expense_claim.submit()
		frappe.set_user("Administrator")


	def test_multiple_payment_entries_against_expense(self):
		# Creating expense claim
		payable_account = get_payable_account("_Test Company")
		employee = make_employee("test_multi_payment@expenseclaim.com", "_Test Company")
		expense_claim = make_expense_claim(
			payable_account, 5500, 5500, "_Test Company", get_expense_account(), employee=employee
		)
		expense_claim.save()
		expense_claim.submit()

		# Payment entry 1: paying 500
		pe1 = make_claim_payment_entry(expense_claim, 500)
		pe1.reload()
		self.assertEqual(pe1.references[0].outstanding_amount, 5500)

		expense_claim.reload()
		outstanding_amount = get_outstanding_amount_for_claim(expense_claim)
		self.assertEqual(outstanding_amount, 5000)
		self.assertEqual(expense_claim.total_amount_reimbursed, 500)
		self.assertEqual(expense_claim.status, "Partially Paid")

		# Payment entry 2: paying 2000
		pe2 = make_claim_payment_entry(expense_claim, 2000)
		pe2.reload()
		self.assertEqual(pe2.references[0].outstanding_amount, 5000)

		expense_claim.reload()
		outstanding_amount = get_outstanding_amount_for_claim(expense_claim)
		self.assertEqual(outstanding_amount, 3000)
		self.assertEqual(expense_claim.total_amount_reimbursed, 2500)
		self.assertEqual(expense_claim.status, "Partially Paid")

		# Payment entry 3: paying 3000
		pe3 = make_claim_payment_entry(expense_claim, 3000)
		pe3.reload()
		self.assertEqual(pe3.references[0].outstanding_amount, 3000)

		expense_claim.reload()
		outstanding_amount = get_outstanding_amount_for_claim(expense_claim)
		self.assertEqual(outstanding_amount, 0)
		self.assertEqual(expense_claim.total_amount_reimbursed, 5500)
		self.assertEqual(expense_claim.status, "Paid")


	def test_rounding(self):
		payable_account = get_payable_account(company_name)
		taxes = generate_taxes(rate=7)
		expense_claim = make_expense_claim(
			payable_account,
			130.84,
			130.84,
			company_name,
			get_expense_account(company_name),
			taxes=taxes,
		)

		self.assertEqual(expense_claim.total_sanctioned_amount, 130.84)
		self.assertEqual(expense_claim.total_taxes_and_charges, 9.16)
		self.assertEqual(expense_claim.grand_total, 140)

		pe = make_claim_payment_entry(expense_claim, 140)

		expense_claim.reload()
		self.assertEqual(expense_claim.status, "Paid")

		pe.cancel()
		expense_claim.reload()
		self.assertEqual(expense_claim.status, "Unpaid")


	def test_company_department_validation(self):
		# validate company and department
		expense_claim = frappe.new_doc("Expense Claim")
		expense_claim.company = "_Test Company 3"
		expense_claim.department = "Accounts - _TC2"
		self.assertRaises(MismatchError, expense_claim.save)


	def test_company_must_match_employee_company(self):
		employee = make_employee("test_other_company_claim@example.com", company="_Test Company 3")
		payable_account = get_payable_account("_Test Company")
		expense_claim = make_expense_claim(
			payable_account,
			300,
			200,
			"_Test Company",
			get_expense_account(),
			do_not_submit=True,
			employee=employee,
		)
		self.assertRaises(MismatchError, expense_claim.save)


	def test_employee_can_only_claim_for_self(self):
		employee = make_employee("test_self_claim@example.com", company="_Test Company")
		other_employee = make_employee("test_other_claim@example.com", company="_Test Company")
		hr_user = make_user("test_hr_claim@example.com", "HR User")
		payable_account = get_payable_account("_Test Company")

		def claim_for(claim_employee):
			return make_expense_claim(
				payable_account,
				300,
				200,
				"_Test Company",
				get_expense_account(),
				do_not_submit=True,
				employee=claim_employee,
			)

		# user permissions are bypassed so that the controller check is what gets exercised
		frappe.db.delete("User Permission", {"user": "test_self_claim@example.com"})
		frappe.clear_cache(user="test_self_claim@example.com")

		frappe.set_user("test_self_claim@example.com")
		self.assertRaises(frappe.PermissionError, claim_for(other_employee).insert)

		own_claim = claim_for(employee).insert()
		own_claim.employee = other_employee
		self.assertRaises(frappe.PermissionError, own_claim.save)

		frappe.set_user(hr_user)
		claim_for(other_employee).insert()

		frappe.set_user("Administrator")


	def test_self_expense_approval(self):
		frappe.db.set_single_value("HR Settings", "prevent_self_expense_approval", 0)

		employee = frappe.get_doc(
			"Employee",
			make_employee("test_self_expense_approval@example.com", "_Test Company"),
		)

		from frappe.utils.user import add_role

		add_role(employee.user_id, "Expense Approver")

		payable_account = get_payable_account("_Test Company")
		expense_claim = make_expense_claim(
			payable_account,
			300,
			200,
			"_Test Company",
			get_expense_account(),
			do_not_submit=True,
			employee=employee.name,
		)

		frappe.set_user(employee.user_id)
		expense_claim.submit()

		self.assertEqual(1, expense_claim.docstatus)


	def test_self_expense_approval_not_allowed(self):
		frappe.db.set_single_value("HR Settings", "prevent_self_expense_approval", 1)

		expense_approver = "test_expense_approver@example.com"
		make_employee(expense_approver, company="_Test Company")

		employee = frappe.get_doc(
			"Employee",
			make_employee(
				"test_self_expense_approval@example.com",
				company="_Test Company",
				expense_approver=expense_approver,
			),
		)

		from frappe.utils.user import add_role

		add_role(employee.user_id, "Expense Approver")
		add_role(expense_approver, "Expense Approver")

		payable_account = get_payable_account("_Test Company")
		expense_claim = make_expense_claim(
			payable_account,
			300,
			200,
			"_Test Company",
			get_expense_account(),
			do_not_submit=True,
			employee=employee.name,
		)

		expense_claim.expense_approver = expense_approver
		expense_claim.save()

		frappe.set_user(employee.user_id)

		self.assertRaises(frappe.ValidationError, expense_claim.submit)
		expense_claim.reload()

		frappe.set_user(expense_approver)
		expense_claim.submit()

		self.assertEqual(1, expense_claim.docstatus)


	def test_advance_in_different_currency_excluded_from_claim(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			create_advance_account,
			make_employee_advance,
			make_payment_entry,
		)

		company = "_Test Company"
		company_currency = get_company_currency(company)
		advance_account = create_advance_account("Employee Advance (USD)", "USD")
		employee = make_employee(
			"test_adv_cross_currency@example.com",
			company,
			salary_currency="USD",
			employee_advance_account=advance_account,
		)
		advance = make_employee_advance(employee)
		make_payment_entry(advance, advance.advance_amount)
		advance.reload()
		self.assertNotEqual(advance.currency, company_currency)

		claim = make_expense_claim(
			get_payable_account(company),
			advance.advance_amount,
			advance.advance_amount,
			company,
			get_expense_account(),
			args={"currency": company_currency, "exchange_rate": 1},
			employee=employee,
			do_not_submit=True,
		)

		# mismatched-currency advance is excluded from the bulk fetch
		self.assertEqual(get_advances(claim), [])
		# explicit fetch raises a currency-specific error
		self.assertRaises(frappe.ValidationError, get_advances, claim, advance.name)


	@HRMSTestSuite.change_settings("HR Settings", {"enable_multi_currency_expense_claim": 1})
	def test_multicurrency_claim(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			create_advance_account,
			get_advances_for_claim,
			make_employee_advance,
			make_payment_entry,
		)

		advance_account = create_advance_account("Employee Advance (USD)", "USD")
		employee = make_employee(
			"test_adv_in_multicurrency@example.com",
			"_Test Company",
			salary_currency="USD",
			employee_advance_account=advance_account,
		)
		advance = make_employee_advance(employee)
		self.assertEqual(advance.status, "Unpaid")

		payment_entry = make_payment_entry(advance, advance.advance_amount)
		advance.reload()
		self.assertEqual(advance.status, "Paid")
		self.assertEqual(payment_entry.paid_amount, advance.paid_amount)

		expected_base_paid = flt(
			advance.paid_amount * payment_entry.exchange_rate,
			advance.precision("base_paid_amount"),
		)
		self.assertEqual(advance.base_paid_amount, expected_base_paid)
		self.assertEqual(payment_entry.base_paid_amount, expected_base_paid)

		payable_account = create_ledger_account("Payroll Payable (USD)", "_Test Company", "Payable", "USD")
		claim_account = create_ledger_account("Travel Expenses (USD)", "_Test Company", "Expense", "USD")
		claim = make_expense_claim(
			payable_account,
			advance.advance_amount,
			advance.advance_amount,
			"_Test Company",
			claim_account,
			args={
				"currency": advance.currency,
				"exchange_rate": get_exchange_rate(
					advance.currency, get_company_currency("_Test Company"), today()
				),
			},
			employee=employee,
			do_not_submit=True,
		)

		claim = get_advances_for_claim(claim, advance.name)
		claim.save().submit()
		claim.reload()
		advance.reload()
		self.assertEqual(claim.status, "Paid")
		self.assertEqual(claim.currency, advance.currency)
		self.assertEqual(advance.status, "Claimed")
		self.assertEqual(claim.total_sanctioned_amount, advance.advance_amount)

		for expense in claim.expenses:
			base_amount = flt(expense.amount * claim.exchange_rate, expense.precision("base_amount"))
			base_sanctioned = flt(
				expense.sanctioned_amount * claim.exchange_rate, expense.precision("base_sanctioned_amount")
			)
			self.assertEqual(expense.base_amount, base_amount)
			self.assertEqual(expense.base_sanctioned_amount, base_sanctioned)

		for claim_advance in claim.advances:
			base_advance_paid = flt(
				claim_advance.advance_paid * claim_advance.exchange_rate,
				claim_advance.precision("base_advance_paid"),
			)
			base_unclaimed_amount = flt(
				claim_advance.unclaimed_amount * claim_advance.exchange_rate,
				claim_advance.precision("base_unclaimed_amount"),
			)
			base_allocated_amount = flt(
				claim_advance.allocated_amount * claim.exchange_rate,
				claim_advance.precision("base_allocated_amount"),
			)
			self.assertEqual(claim_advance.base_advance_paid, base_advance_paid)
			self.assertEqual(claim_advance.base_unclaimed_amount, base_unclaimed_amount)
			self.assertEqual(claim_advance.base_allocated_amount, base_allocated_amount)

		total_base_sanctioned = flt(
			claim.total_sanctioned_amount * claim.exchange_rate,
			claim.precision("base_total_sanctioned_amount"),
		)
		total_advance_amount = flt(
			claim.total_advance_amount * claim.exchange_rate, claim.precision("base_total_advance_amount")
		)
		grand_total = flt(claim.grand_total * claim.exchange_rate, claim.precision("base_grand_total"))
		total_claimed_amount = flt(
			claim.total_claimed_amount * claim.exchange_rate, claim.precision("base_total_claimed_amount")
		)
		self.assertEqual(claim.base_total_sanctioned_amount, total_base_sanctioned)
		self.assertEqual(claim.base_total_advance_amount, total_advance_amount)
		self.assertEqual(claim.base_grand_total, grand_total)
		self.assertEqual(claim.base_total_claimed_amount, total_claimed_amount)
		self.assertEqual(claim.total_exchange_gain_loss, 0)


	@HRMSTestSuite.change_settings("HR Settings", {"enable_multi_currency_expense_claim": 1})
	def test_advance_claim_multicurrency_gain_loss(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			create_advance_account,
			get_advances_for_claim,
			make_employee_advance,
			make_payment_entry,
		)

		advance_account = create_advance_account("Employee Advance (USD)", "USD")
		employee = make_employee(
			"test_advance_claim_gain_loss_multicurrency@example.com",
			"_Test Company",
			salary_currency="USD",
			employee_advance_account=advance_account,
		)
		advance = make_employee_advance(employee)
		self.assertEqual(advance.status, "Unpaid")

		make_payment_entry(advance, advance.advance_amount)
		advance.reload()
		self.assertEqual(advance.status, "Paid")

		payable_account = create_ledger_account("Payroll Payable (USD)", "_Test Company", "Payable", "USD")
		claim_account = create_ledger_account("Travel Expenses (USD)", "_Test Company", "Expense", "USD")
		claim = make_expense_claim(
			payable_account,
			advance.advance_amount,
			advance.advance_amount,
			"_Test Company",
			claim_account,
			args={"currency": advance.currency, "exchange_rate": 65},
			employee=employee,
			do_not_submit=True,
		)

		claim = get_advances_for_claim(claim, advance.name)
		claim.save().submit()
		claim.reload()
		advance.reload()
		self.assertEqual(claim.status, "Paid")
		self.assertEqual(advance.status, "Claimed")

		for claim_advance in claim.advances:
			self.assertEqual(claim_advance.exchange_gain_loss, 2100)
		self.assertEqual(claim.total_exchange_gain_loss, 2100)

		# the gain is booked within the claim's own (balanced) ledger entries
		entries = frappe.get_all(
			"HR Ledger Entry",
			filters={"voucher_type": "Expense Claim", "voucher_no": claim.name, "is_cancelled": 0},
			fields=["account", "debit", "credit"],
		)
		gain_loss = [e for e in entries if e.account == claim.gain_loss_account]
		self.assertEqual(len(gain_loss), 1)
		self.assertEqual(gain_loss[0].credit, 2100)
		self.assertEqual(sum(e.debit for e in entries), sum(e.credit for e in entries))


	def test_no_exchange_gain_loss_when_advance_exchange_rate_missing(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			get_advances_for_claim,
			make_employee_advance,
			make_payment_entry,
		)

		employee = make_employee("test_advance_claim_missing_exchange_rate@example.com", "_Test Company")
		advance = make_employee_advance(employee)
		self.assertEqual(advance.status, "Unpaid")

		make_payment_entry(advance, advance.advance_amount)
		advance.reload()
		self.assertEqual(advance.status, "Paid")

		claim = make_expense_claim(
			get_payable_account("_Test Company"),
			advance.advance_amount,
			advance.advance_amount,
			"_Test Company",
			get_expense_account(),
			employee=employee,
			do_not_submit=True,
		)

		claim = get_advances_for_claim(claim, advance.name)
		for claim_advance in claim.advances:
			claim_advance.exchange_rate = 0

		claim.save().submit()
		claim.reload()

		for claim_advance in claim.advances:
			self.assertEqual(claim_advance.exchange_gain_loss, 0)
		self.assertEqual(claim.total_exchange_gain_loss, 0)
		self.assertFalse(claim.gain_loss_account)


	def test_status_on_discard(self):
		payable_account = get_payable_account(company_name)
		expense_claim = make_expense_claim(
			payable_account, 300, 200, company_name, get_expense_account(company_name), do_not_submit=True
		)
		expense_claim.insert()
		expense_claim.reload()
		self.assertEqual(expense_claim.status, "Draft")
		expense_claim.discard()
		expense_claim.reload()
		self.assertEqual(expense_claim.status, "Cancelled")


def get_payable_account(company):
	return frappe.get_cached_value(
		"Company", company, "default_expense_claim_payable_account"
	) or create_default_accounts(company).get("Expense Claims Payable")


def get_expense_account(company="_Test Company"):
	return create_ledger_account("Travel Expenses", company, "Expense")


def generate_taxes(company=None, rate=None) -> dict:
	company = company or company_name
	account = create_ledger_account("Output Tax CGST", company, "Liability")
	cost_center = frappe.db.get_value("Company", company, "cost_center")

	return {
		"taxes": [
			{
				"account_head": account,
				"cost_center": cost_center,
				"rate": rate or 9,
				"description": "CGST",
			}
		]
	}


def make_expense_claim(
	payable_account,
	amount,
	sanctioned_amount,
	company,
	account,
	args=None,
	do_not_submit=False,
	taxes=None,
	employee=None,
	approval_status="Approved",
):
	if not employee:
		employee = frappe.db.get_value("Employee", {"status": "Active", "company": company})
		if not employee:
			employee = make_employee("test_employee@expenseclaim.com", company=company)

	currency, cost_center = frappe.db.get_value("Company", company, ["default_currency", "cost_center"])
	expense_claim = {
		"doctype": "Expense Claim",
		"employee": employee,
		"payable_account": payable_account,
		"approval_status": approval_status,
		"company": company,
		"currency": currency,
		"exchange_rate": 1,
		"expenses": [
			{
				"expense_type": "Travel",
				"default_account": account,
				"currency": currency,
				"amount": amount,
				"sanctioned_amount": sanctioned_amount,
				"cost_center": cost_center,
			}
		],
	}
	if taxes:
		expense_claim.update(taxes)

	if args:
		expense_claim.update(args)
	expense_claim = frappe.get_doc(expense_claim)

	if do_not_submit:
		return expense_claim
	expense_claim.submit()
	return expense_claim


def make_claim_payment_entry(expense_claim, amount):
	"""Reimburse the claim through a submitted HR Payment."""
	payment = make_payment(expense_claim.name)
	payment.reference_no = "1"
	payment.reference_date = nowdate()
	payment.references[0].allocated_amount = amount
	payment.paid_amount = amount
	payment.insert()
	payment.submit()

	return payment
