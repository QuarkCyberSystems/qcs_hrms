# Copyright (c) 2026, Quark Cyber System FZC and contributors
# For license information, please see license.txt

"""REST API for integrating the HR ledger with external accounting systems.

All methods are available at `/api/method/qcs_hrms.api.ledger.<method>`.
See `apps/qcs_hrms/docs/ledger_api.md`.
"""

import json

import frappe
from frappe import _
from frappe.query_builder import Order
from frappe.query_builder.functions import Min
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime

import qcs_hrms
from qcs_hrms.ledger import utils as ledger

LEDGER_ENTRY = "HR Ledger Entry"
MAX_PAGE_LENGTH = 1000
SYNC_STATUSES = ("Pending", "Synced", "Failed", "Skipped")
ENTRY_FIELDS = (
	"name",
	"batch_id",
	"posting_date",
	"company",
	"account",
	"account_currency",
	"debit",
	"credit",
	"debit_in_account_currency",
	"credit_in_account_currency",
	"party_type",
	"party",
	"cost_center",
	"against",
	"voucher_type",
	"voucher_no",
	"voucher_detail_no",
	"against_voucher_type",
	"against_voucher",
	"remarks",
	"is_cancelled",
	"is_reversal",
	"reversal_of",
	"sync_status",
	"external_reference",
	"synced_on",
	"sync_error",
	"modified",
)


def _parse_list(value, label) -> list:
	if value is None or value == "":
		return []
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			value = [v.strip() for v in value.split(",") if v.strip()]
	if isinstance(value, str):
		value = [value]
	if not isinstance(value, list | tuple):
		frappe.throw(_("{0} must be a list").format(label))
	return list(value)


def _check_company(company: str | None):
	if company:
		if not frappe.db.exists("Company", company):
			frappe.throw(_("Company {0} does not exist").format(company), frappe.DoesNotExistError)
		frappe.has_permission("Company", "read", company, throw=True)


def _check_read():
	frappe.has_permission(LEDGER_ENTRY, "read", throw=True)


def _paging(start, page_length) -> tuple[int, int]:
	start = max(cint(start), 0)
	page_length = cint(page_length) or 500
	return start, min(max(page_length, 1), MAX_PAGE_LENGTH)


def _conditions(
	le,
	company=None,
	from_date=None,
	to_date=None,
	voucher_type=None,
	voucher_no=None,
	sync_status=None,
	modified_since=None,
	account=None,
	party_type=None,
	party=None,
	include_cancelled=True,
):
	conditions = []
	if company:
		conditions.append(le.company == company)
	if from_date:
		conditions.append(le.posting_date >= getdate(from_date))
	if to_date:
		conditions.append(le.posting_date <= getdate(to_date))
	if voucher_type:
		conditions.append(le.voucher_type == voucher_type)
	if voucher_no:
		conditions.append(le.voucher_no == voucher_no)
	if sync_status:
		statuses = _parse_list(sync_status, "sync_status")
		invalid = set(statuses) - set(SYNC_STATUSES)
		if invalid:
			frappe.throw(_("Invalid sync_status {0}").format(", ".join(invalid)))
		conditions.append(le.sync_status.isin(statuses))
	if modified_since:
		conditions.append(le.modified >= get_datetime(modified_since))
	if account:
		conditions.append(le.account == account)
	if party_type:
		conditions.append(le.party_type == party_type)
	if party:
		conditions.append(le.party == party)
	if not cint(include_cancelled):
		conditions.append(le.is_cancelled == 0)

	# restrict to companies the user may read (user permissions)
	allowed = frappe.get_list("Company", pluck="name", limit_page_length=0)
	conditions.append(le.company.isin(allowed or [""]))
	return conditions


def _entry_query(conditions=None):
	le = frappe.qb.DocType(LEDGER_ENTRY)
	acc = frappe.qb.DocType("HR Ledger Account")
	query = (
		frappe.qb.from_(le)
		.left_join(acc)
		.on(acc.name == le.account)
		.select(*[le[f] for f in ENTRY_FIELDS], acc.account_number, acc.account_name, acc.account_type)
	)
	for condition in conditions or []:
		query = query.where(condition)
	return query, le


@frappe.whitelist()
def get_accounts(company: str | None = None, include_disabled: int = 0) -> list[dict]:
	"""List HR Ledger Accounts (with external account numbers) for mapping."""
	frappe.has_permission("HR Ledger Account", "read", throw=True)
	_check_company(company)
	filters = {}
	if company:
		filters["company"] = company
	if not cint(include_disabled):
		filters["disabled"] = 0
	return frappe.get_list(
		"HR Ledger Account",
		filters=filters,
		fields=["name", "account_name", "account_number", "company", "account_type", "account_currency", "disabled"],
		order_by="company asc, account_name asc",
		limit_page_length=0,
	)


@frappe.whitelist()
def get_ledger_entries(
	company: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	voucher_type: str | None = None,
	voucher_no: str | None = None,
	sync_status: str | list | None = None,
	modified_since: str | None = None,
	account: str | None = None,
	party_type: str | None = None,
	party: str | None = None,
	include_cancelled: int = 1,
	start: int = 0,
	page_length: int = 500,
) -> dict:
	"""Flat list of ledger entries with paging. Cancelled originals and their reversals are included
	by default so that an integration sees the full history."""
	_check_read()
	_check_company(company)
	start, page_length = _paging(start, page_length)
	le = frappe.qb.DocType(LEDGER_ENTRY)
	conditions = _conditions(
		le,
		company,
		from_date,
		to_date,
		voucher_type,
		voucher_no,
		sync_status,
		modified_since,
		account,
		party_type,
		party,
		include_cancelled,
	)
	query, le = _entry_query(conditions)
	rows = (
		query.orderby(le.creation, order=Order.asc)
		.orderby(le.name, order=Order.asc)
		.limit(page_length + 1)
		.offset(start)
		.run(as_dict=True)
	)
	has_more = len(rows) > page_length
	return {"entries": rows[:page_length], "start": start, "page_length": page_length, "has_more": has_more}


@frappe.whitelist()
def get_vouchers(
	company: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	voucher_type: str | None = None,
	sync_status: str | list | None = "Pending",
	modified_since: str | None = None,
	start: int = 0,
	page_length: int = 100,
) -> dict:
	"""Journal-shaped payload: one balanced voucher per posting batch.

	Each voucher has `batch_id` (pass it to `mark_synced`), the source document, posting date,
	totals and `lines`. A cancellation produces a separate voucher with `is_reversal = 1`.
	"""
	_check_read()
	_check_company(company)
	start, page_length = _paging(start, page_length)
	le = frappe.qb.DocType(LEDGER_ENTRY)
	first_creation = Min(le.creation)
	batch_query = frappe.qb.from_(le).select(le.batch_id, first_creation)
	for condition in _conditions(
		le, company, from_date, to_date, voucher_type, None, sync_status, modified_since
	):
		batch_query = batch_query.where(condition)
	# page on batches (vouchers), not on lines
	batches = (
		batch_query.groupby(le.batch_id)
		.orderby(first_creation, order=Order.asc)
		.orderby(le.batch_id, order=Order.asc)
		.limit(page_length + 1)
		.offset(start)
		.run(pluck=True)
	)
	has_more = len(batches) > page_length
	page = batches[:page_length]
	if not page:
		return {"vouchers": [], "start": start, "page_length": page_length, "has_more": False}

	lines_query, le = _entry_query()
	lines = lines_query.where(le.batch_id.isin(page)).orderby(le.creation).orderby(le.name).run(as_dict=True)

	vouchers = {batch: None for batch in page}
	for line in lines:
		voucher = vouchers.get(line.batch_id)
		if voucher is None:
			voucher = vouchers[line.batch_id] = frappe._dict(
				{
					"batch_id": line.batch_id,
					"company": line.company,
					"currency": qcs_hrms.get_company_currency(line.company),
					"posting_date": line.posting_date,
					"voucher_type": line.voucher_type,
					"voucher_no": line.voucher_no,
					"is_reversal": line.is_reversal,
					"remarks": line.remarks,
					"total_debit": 0.0,
					"total_credit": 0.0,
					"sync_status": line.sync_status,
					"external_reference": line.external_reference,
					"lines": [],
				}
			)
		voucher.total_debit += flt(line.debit)
		voucher.total_credit += flt(line.credit)
		if line.sync_status != voucher.sync_status:
			voucher.sync_status = "Mixed"
		voucher.lines.append(line)

	precision = ledger.get_ledger_precision()
	result = []
	for voucher in vouchers.values():
		if not voucher:
			continue
		voucher.total_debit = flt(voucher.total_debit, precision)
		voucher.total_credit = flt(voucher.total_credit, precision)
		voucher.is_balanced = voucher.total_debit == voucher.total_credit
		result.append(voucher)

	return {"vouchers": result, "start": start, "page_length": page_length, "has_more": has_more}


def _resolve_entries(entries=None, vouchers=None) -> list[str]:
	names = set(_parse_list(entries, "entries"))
	batch_ids = _parse_list(vouchers, "vouchers")
	if not names and not batch_ids:
		frappe.throw(_("Pass either entries (HR Ledger Entry names) or vouchers (batch ids)"))
	if batch_ids:
		names |= set(frappe.get_all(LEDGER_ENTRY, filters={"batch_id": ["in", batch_ids]}, pluck="name"))
	if not names:
		frappe.throw(_("No ledger entries found"), frappe.DoesNotExistError)

	found = frappe.get_all(
		LEDGER_ENTRY, filters={"name": ["in", list(names)]}, fields=["name", "company", "sync_status"]
	)
	missing = names - {d.name for d in found}
	if missing:
		frappe.throw(_("Ledger entries not found: {0}").format(", ".join(sorted(missing))), frappe.DoesNotExistError)
	for company in {d.company for d in found}:
		frappe.has_permission("Company", "read", company, throw=True)
	return [d.name for d in found if d.sync_status != "Skipped"]


def _check_sync_permission():
	frappe.only_for(("System Manager", "Accounts Manager", "Accounts User", "HR Manager"))


@frappe.whitelist(methods=["POST"])
def mark_synced(
	entries: str | list | None = None, vouchers: str | list | None = None, external_reference: str | None = None
) -> dict:
	"""Mark ledger entries (or whole vouchers by batch_id) as posted in the external system."""
	_check_sync_permission()
	names = _resolve_entries(entries, vouchers)
	ledger.mark_entries_synced(names, external_reference)
	return {"updated": len(names), "sync_status": "Synced"}


@frappe.whitelist(methods=["POST"])
def mark_sync_failed(
	entries: str | list | None = None, vouchers: str | list | None = None, error: str | None = None
) -> dict:
	"""Record a failed sync attempt; failed entries are returned again with sync_status=Failed."""
	_check_sync_permission()
	names = _resolve_entries(entries, vouchers)
	for name in names:
		frappe.db.set_value(
			LEDGER_ENTRY,
			name,
			{"sync_status": "Failed", "sync_error": (error or "")[:10000], "synced_on": now_datetime()},
		)
	return {"updated": len(names), "sync_status": "Failed"}


@frappe.whitelist()
def get_party_balance(
	party: str, company: str, party_type: str = "Employee", account: str | None = None
) -> dict:
	"""Balance (debit - credit, company currency) of a party; positive means the party owes the company."""
	_check_read()
	_check_company(company)
	frappe.has_permission(party_type, "read", party, throw=True)
	return {
		"party_type": party_type,
		"party": party,
		"company": company,
		"account": account,
		"currency": qcs_hrms.get_company_currency(company),
		"balance": ledger.get_party_balance(party_type, party, company, account),
	}


OUTSTANDING_DOCTYPES = {
	# doctype: (payment_type, date field)
	"Expense Claim": ("Pay", "posting_date"),
	"Employee Advance": (None, "posting_date"),
	"Leave Encashment": ("Pay", "encashment_date"),
	"Full and Final Statement": (None, "transaction_date"),
	"Gratuity": ("Pay", "posting_date"),
	"Salary Slip": ("Pay", "posting_date"),
}

# extra filters: only salary slips whose payroll accrual was posted have a payable in the ledger
OUTSTANDING_FILTERS = {"Salary Slip": {"accrual_posted": 1}}


@frappe.whitelist()
def get_outstanding_documents(employee: str, company: str | None = None) -> list[dict]:
	"""Submitted HR documents of an employee that still have an amount to pay or receive."""
	frappe.has_permission("Employee", "read", employee, throw=True)
	_check_company(company)

	result = []
	for doctype, (payment_type, date_field) in OUTSTANDING_DOCTYPES.items():
		if not frappe.db.exists("DocType", doctype) or not frappe.has_permission(doctype, "read"):
			continue
		filters = {"employee": employee, "docstatus": 1, **OUTSTANDING_FILTERS.get(doctype, {})}
		if company:
			filters["company"] = company
		meta = frappe.get_meta(doctype)
		fields = ["name", "company"] + [f for f in (date_field, "currency", "status") if meta.has_field(f)]
		for doc in frappe.get_list(doctype, filters=filters, fields=fields, limit_page_length=0):
			for ptype in [payment_type] if payment_type else ["Pay", "Receive"]:
				outstanding = ledger.get_outstanding_amount(doctype, doc.name, ptype)
				if flt(outstanding) <= 0:
					continue
				result.append(
					{
						"reference_doctype": doctype,
						"reference_name": doc.name,
						"company": doc.company,
						"date": doc.get(date_field),
						"currency": doc.get("currency") or qcs_hrms.get_company_currency(doc.company),
						"status": doc.get("status"),
						"payment_type": ptype,
						"outstanding_amount": outstanding,
					}
				)
	return result


@frappe.whitelist(methods=["POST"])
def record_payment(
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	amount: float | None = None,
	payment_type: str = "Pay",
	company: str | None = None,
	party_type: str = "Employee",
	party: str | None = None,
	references: str | list | None = None,
	paid_from: str | None = None,
	paid_to: str | None = None,
	bank_account: str | None = None,
	posting_date: str | None = None,
	mode_of_payment: str | None = None,
	reference_no: str | None = None,
	reference_date: str | None = None,
	remarks: str | None = None,
	currency: str | None = None,
	exchange_rate: float | None = None,
	submit: int = 1,
) -> dict:
	"""Create (and submit) an HR Payment for a payment made / received in an external system.

	Either pass one `reference_doctype` + `reference_name` (+ optional `amount`), or `references`
	as a list of {reference_doctype, reference_name, allocated_amount} together with `party`.
	"""
	if payment_type not in ("Pay", "Receive"):
		frappe.throw(_("payment_type must be Pay or Receive"))
	frappe.has_permission("HR Payment", "create", throw=True)
	if cint(submit):
		frappe.has_permission("HR Payment", "submit", throw=True)

	refs = [frappe._dict(r) for r in _parse_list(references, "references")]
	if reference_doctype and reference_name:
		refs.insert(
			0,
			frappe._dict(
				reference_doctype=reference_doctype, reference_name=reference_name, allocated_amount=amount
			),
		)
	for ref in refs:
		if not (ref.reference_doctype and ref.reference_name):
			frappe.throw(_("Each reference needs reference_doctype and reference_name"))
		frappe.has_permission(ref.reference_doctype, "read", ref.reference_name, throw=True)

	if refs:
		first = refs[0]
		payment = ledger.make_hr_payment(
			first.reference_doctype,
			first.reference_name,
			payment_type,
			first.allocated_amount if first.allocated_amount not in (None, "") else None,
			bank_account,
		)
		for ref in refs[1:]:
			details = ledger.get_reference_details(ref.reference_doctype, ref.reference_name, payment_type)
			allocated = flt(ref.allocated_amount) if ref.allocated_amount not in (None, "") else details.outstanding_amount
			payment.append(
				"references",
				{
					"reference_doctype": ref.reference_doctype,
					"reference_name": ref.reference_name,
					"outstanding_amount": details.outstanding_amount,
					"allocated_amount": allocated,
				},
			)
		payment.paid_amount = flt(amount) if (amount and len(refs) > 1) else sum(
			flt(r.allocated_amount) for r in payment.references
		)
	else:
		if not (company and party and flt(amount) > 0):
			frappe.throw(_("company, party and amount are required for a payment without references"))
		_check_company(company)
		payment = frappe.new_doc("HR Payment")
		payment.update(
			{
				"payment_type": payment_type,
				"company": company,
				"party_type": party_type,
				"party": party,
				"paid_amount": flt(amount),
			}
		)
		bank = bank_account or ledger.get_default_bank_cash_account(company)
		if payment_type == "Pay":
			payment.paid_from = bank
		else:
			payment.paid_to = bank

	values = {
		"posting_date": posting_date,
		"paid_from": paid_from,
		"paid_to": paid_to,
		"mode_of_payment": mode_of_payment,
		"reference_no": reference_no,
		"reference_date": reference_date,
		"remarks": remarks,
		"currency": currency,
		"exchange_rate": flt(exchange_rate) if exchange_rate else None,
	}
	payment.update({k: v for k, v in values.items() if v not in (None, "")})
	payment.insert()
	if cint(submit):
		payment.submit()

	return {
		"name": payment.name,
		"docstatus": payment.docstatus,
		"paid_amount": payment.paid_amount,
		"total_allocated_amount": payment.total_allocated_amount,
		"unallocated_amount": payment.unallocated_amount,
		"ledger_entries": frappe.get_all(
			LEDGER_ENTRY, filters={"voucher_type": "HR Payment", "voucher_no": payment.name}, pluck="name"
		),
	}
