# Copyright (c) 2026, Quark Cyber System FZC and contributors
# For license information, please see license.txt

"""HR ledger: a minimal double-entry ledger used instead of a general ledger.

Every financial effect of an HR document (expense claim booking, advance payment, payroll
accrual, ...) is recorded as balanced `HR Ledger Entry` rows. External accounting systems
read those rows through `qcs_hrms.api.ledger` and mark them as synced.

Sign conventions: `get_party_balance` returns debit - credit (positive: the party owes the
company, negative: the company owes the party).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Sum
from frappe.utils import cint, flt, get_link_to_form, getdate, now_datetime, nowdate

import qcs_hrms

LEDGER_ENTRY = "HR Ledger Entry"

# fields copied from the source doc / args into a ledger entry
ENTRY_FIELDS = (
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
)

# entries that share these values are merged into one line
MERGE_KEYS = (
	"account",
	"party_type",
	"party",
	"cost_center",
	"against_voucher_type",
	"against_voucher",
	"voucher_detail_no",
)

# candidate fieldnames holding the party-side account on documents referenced by an HR Payment
PARTY_ACCOUNT_FIELDS = (
	"payable_account",
	"advance_account",
	"payroll_payable_account",
	"party_account",
	"account",
)


class LedgerImbalanceError(frappe.ValidationError):
	pass


class LedgerController(Document):
	"""Base class for documents that post to the HR ledger (replaces an accounts controller).

	Subclasses implement `get_ledger_entries()` returning a list of dicts built with
	`self.get_ledger_dict(...)`.
	"""

	def get_ledger_entries(self) -> list[dict]:
		raise NotImplementedError

	def make_ledger_entries(self, cancel: bool = False):
		if cancel:
			make_reverse_ledger_entries(self.doctype, self.name)
			return

		entries = self.get_ledger_entries()
		if entries:
			make_ledger_entries(entries)

	def get_ledger_dict(self, args: dict, account_currency: str | None = None, item=None) -> frappe._dict:
		"""Return a ledger entry dict pre-filled from this document.

		`args` holds the account, debit/credit (company currency) and optionally the amounts in
		`account_currency` (the transaction currency). When the account's own currency is the company
		currency, the account-currency amounts are set from the company-currency amounts.
		"""
		company = self.get("company")
		entry = frappe._dict(
			{
				"company": company,
				"posting_date": self.get("posting_date") or self.get("transaction_date") or nowdate(),
				"voucher_type": self.doctype,
				"voucher_no": self.name,
				"remarks": self.get("remarks") or self.get("remark") or self.get("user_remark"),
				"debit": 0,
				"credit": 0,
				"debit_in_account_currency": 0,
				"credit_in_account_currency": 0,
				"cost_center": self.get("cost_center"),
			}
		)
		if item is not None and getattr(item, "doctype", None) != self.doctype and item.get("name"):
			entry.voucher_detail_no = item.name
		entry.update(args)

		company_currency = qcs_hrms.get_company_currency(company)
		account_ccy = get_account_currency(entry.account) or company_currency
		entry.account_currency = account_ccy

		for side in ("debit", "credit"):
			key = f"{side}_in_account_currency"
			if account_ccy == company_currency:
				entry[key] = flt(entry.get(side))
			elif account_currency and account_ccy != account_currency:
				frappe.throw(
					_("Account {0} is in currency {1} but the transaction is in {2}").format(
						frappe.bold(entry.account), account_ccy, account_currency
					)
				)
		return entry

	def get_hr_payment_party_account(self):
		for fieldname in PARTY_ACCOUNT_FIELDS:
			if self.meta.has_field(fieldname) and self.get(fieldname):
				return self.get(fieldname)


def get_account_currency(account: str | None) -> str | None:
	if not account:
		return None
	return frappe.get_cached_value("HR Ledger Account", account, "account_currency")


def get_ledger_precision() -> int:
	return cint(frappe.get_precision(LEDGER_ENTRY, "debit")) or 2


def make_ledger_entries(entries: list[dict], cancel: bool = False, merge_entries: bool = True) -> list[str]:
	"""Post balanced ledger entries (same call shape as a `make_gl_entries`).

	With `cancel=True` the existing entries of the voucher of the first entry are reversed instead.
	Returns the names of the created entries.
	"""
	if not entries:
		return []

	if cancel:
		first = frappe._dict(entries[0])
		return make_reverse_ledger_entries(first.voucher_type, first.voucher_no)

	entries = [prepare_entry(frappe._dict(e)) for e in entries]
	if merge_entries:
		entries = merge_similar_entries(entries)

	entries = [e for e in entries if flt(e.debit) or flt(e.credit)]
	if not entries:
		return []

	validate_entries(entries)
	process_round_off(entries)
	return save_entries(entries)


def prepare_entry(entry: frappe._dict) -> frappe._dict:
	if not entry.account:
		frappe.throw(_("Account is mandatory for ledger entries ({0} {1})").format(entry.voucher_type, entry.voucher_no))
	if not entry.company:
		entry.company = frappe.get_cached_value("HR Ledger Account", entry.account, "company")

	entry.posting_date = getdate(entry.posting_date or nowdate())
	company_currency = qcs_hrms.get_company_currency(entry.company)
	entry.account_currency = get_account_currency(entry.account) or company_currency
	exchange_rate = flt(entry.pop("exchange_rate", 0))

	for side in ("debit", "credit"):
		key = f"{side}_in_account_currency"
		base, in_account = flt(entry.get(side)), flt(entry.get(key))
		if entry.account_currency == company_currency:
			base = base or in_account
			in_account = base
		elif not base and in_account:
			rate = exchange_rate or get_rate(entry.account_currency, company_currency, entry.posting_date)
			base = in_account * rate
		elif base and not in_account:
			rate = exchange_rate or get_rate(entry.account_currency, company_currency, entry.posting_date)
			in_account = base / rate if rate else 0
		entry[side], entry[key] = base, in_account

	# negative amounts flip to the other side
	for side, other in (("debit", "credit"), ("credit", "debit")):
		if flt(entry[side]) < 0:
			entry[other] = flt(entry[other]) - flt(entry[side])
			entry[f"{other}_in_account_currency"] = flt(entry[f"{other}_in_account_currency"]) - flt(
				entry[f"{side}_in_account_currency"]
			)
			entry[side] = 0
			entry[f"{side}_in_account_currency"] = 0
	return entry


def get_rate(from_currency: str, to_currency: str, date=None) -> float:
	if from_currency == to_currency:
		return 1.0
	from qcs_hrms.utils.compat import get_exchange_rate

	rate = flt(get_exchange_rate(from_currency, to_currency, date))
	if not rate:
		frappe.throw(_("Exchange rate from {0} to {1} not found").format(from_currency, to_currency))
	return rate


def merge_similar_entries(entries: list[frappe._dict]) -> list[frappe._dict]:
	merged = {}
	for entry in entries:
		key = tuple(entry.get(k) or None for k in MERGE_KEYS)
		if key not in merged:
			merged[key] = entry
			continue
		target = merged[key]
		for f in ("debit", "credit", "debit_in_account_currency", "credit_in_account_currency"):
			target[f] = flt(target.get(f)) + flt(entry.get(f))
		if entry.get("against") and entry.against not in (target.get("against") or ""):
			target.against = ",".join(filter(None, [target.get("against"), entry.against]))

	result = []
	for entry in merged.values():
		# net out debit and credit on the same line
		for suffix in ("", "_in_account_currency"):
			debit, credit = flt(entry[f"debit{suffix}"]), flt(entry[f"credit{suffix}"])
			net = debit - credit
			entry[f"debit{suffix}"], entry[f"credit{suffix}"] = (net, 0) if net > 0 else (0, -net)
		result.append(entry)
	return result


def validate_entries(entries: list[frappe._dict]):
	precision = get_ledger_precision()
	company = entries[0].company
	for entry in entries:
		for f in ("debit", "credit", "debit_in_account_currency", "credit_in_account_currency"):
			entry[f] = flt(entry.get(f), precision)

		if entry.company != company:
			frappe.throw(_("All ledger entries of a voucher must belong to the same company"))

		account = frappe.get_cached_value(
			"HR Ledger Account", entry.account, ["company", "disabled"], as_dict=True
		)
		if not account:
			frappe.throw(_("HR Ledger Account {0} does not exist").format(frappe.bold(entry.account)))
		if account.company != entry.company:
			frappe.throw(
				_("Account {0} does not belong to company {1}").format(
					frappe.bold(entry.account), frappe.bold(entry.company)
				)
			)
		if account.disabled:
			frappe.throw(_("Account {0} is disabled").format(get_link_to_form("HR Ledger Account", entry.account)))


def process_round_off(entries: list[frappe._dict]):
	precision = get_ledger_precision()
	debit = sum(flt(e.debit) for e in entries)
	credit = sum(flt(e.credit) for e in entries)
	difference = flt(debit - credit, precision)
	if not difference:
		return

	allowance = 0.5 if precision == 0 else 5.0 / (10**precision)
	first = entries[0]
	if abs(difference) > allowance:
		frappe.throw(
			_("Debit ({0}) and Credit ({1}) are not equal for {2} {3}. Difference is {4}.").format(
				debit, credit, first.voucher_type, first.voucher_no, difference
			),
			LedgerImbalanceError,
			title=_("Ledger Imbalance"),
		)

	round_off_account, round_off_cost_center = frappe.get_cached_value(
		"Company", first.company, ["round_off_account", "round_off_cost_center"]
	)
	if not round_off_account:
		frappe.throw(
			_("Please set the Round Off Account in Company {0}").format(get_link_to_form("Company", first.company)),
			LedgerImbalanceError,
		)

	round_off = frappe._dict(
		{
			"company": first.company,
			"posting_date": first.posting_date,
			"voucher_type": first.voucher_type,
			"voucher_no": first.voucher_no,
			"account": round_off_account,
			"account_currency": get_account_currency(round_off_account)
			or qcs_hrms.get_company_currency(first.company),
			"cost_center": round_off_cost_center or first.cost_center,
			"remarks": _("Round off"),
			"debit": abs(difference) if difference < 0 else 0,
			"credit": difference if difference > 0 else 0,
		}
	)
	round_off.debit_in_account_currency = round_off.debit
	round_off.credit_in_account_currency = round_off.credit
	entries.append(round_off)


def save_entries(entries: list[frappe._dict], batch_id: str | None = None, **extra) -> list[str]:
	batch_id = batch_id or frappe.generate_hash(length=12)
	names = []
	for entry in entries:
		doc = frappe.new_doc(LEDGER_ENTRY)
		for f in ENTRY_FIELDS:
			if entry.get(f) is not None:
				doc.set(f, entry.get(f))
		doc.batch_id = batch_id
		doc.sync_status = entry.get("sync_status") or "Pending"
		for key, value in extra.items():
			doc.set(key, value)
		doc.flags.from_ledger_utils = True
		doc.insert(ignore_permissions=True, ignore_links=True)
		names.append(doc.name)
	return names


def make_reverse_ledger_entries(voucher_type: str, voucher_no: str, posting_date=None) -> list[str]:
	"""Cancel all active ledger entries of a voucher and post reversals.

	Entries never synced to an external system (Pending/Failed) are marked Skipped together with
	their reversal; reversals of Synced entries are Pending so the integration can post them.
	"""
	le = frappe.qb.DocType(LEDGER_ENTRY)
	entries = (
		frappe.qb.from_(le)
		.select("*")
		.where((le.voucher_type == voucher_type) & (le.voucher_no == voucher_no) & (le.is_cancelled == 0))
		.for_update()
		.run(as_dict=True)
	)
	if not entries:
		return []

	posting_date = getdate(posting_date or nowdate())
	# one reversal batch per original batch, so that each reversal voucher balances on its own and
	# carries a single sync status (an original batch may be synced while another is not)
	reversal_batches = {}
	names = []
	for entry in entries:
		was_synced = entry.sync_status == "Synced"
		original_update = {"is_cancelled": 1}
		if not was_synced:
			original_update["sync_status"] = "Skipped"
		frappe.db.set_value(LEDGER_ENTRY, entry.name, original_update, update_modified=True)

		reversal = frappe._dict({f: entry.get(f) for f in ENTRY_FIELDS})
		reversal.update(
			{
				"posting_date": posting_date,
				"debit": entry.credit,
				"credit": entry.debit,
				"debit_in_account_currency": entry.credit_in_account_currency,
				"credit_in_account_currency": entry.debit_in_account_currency,
				"remarks": _("On cancellation of {0} {1}").format(voucher_type, voucher_no),
				"sync_status": "Pending" if was_synced else "Skipped",
			}
		)
		if entry.batch_id not in reversal_batches:
			reversal_batches[entry.batch_id] = frappe.generate_hash(length=12)
		names += save_entries(
			[reversal],
			batch_id=reversal_batches[entry.batch_id],
			is_cancelled=1,
			is_reversal=1,
			reversal_of=entry.name,
		)
	return names


def get_party_balance(
	party_type: str, party: str, company: str, account: str | None = None, in_account_currency: bool = False
) -> float:
	"""Return debit - credit for the party (positive: party owes the company)."""
	le = frappe.qb.DocType(LEDGER_ENTRY)
	debit, credit = (
		(le.debit_in_account_currency, le.credit_in_account_currency)
		if in_account_currency
		else (le.debit, le.credit)
	)
	query = (
		frappe.qb.from_(le)
		.select(Sum(debit - credit))
		.where(
			(le.party_type == party_type)
			& (le.party == party)
			& (le.company == company)
			& (le.is_cancelled == 0)
		)
	)
	if account:
		query = query.where(le.account == account)
	return flt(query.run()[0][0])


def get_voucher_balance(
	voucher_type: str, voucher_no: str, exclude_own_entries: bool = True, account: str | None = None
) -> float:
	"""Sum of (debit - credit) in account currency of party entries posted *against* a voucher."""
	le = frappe.qb.DocType(LEDGER_ENTRY)
	query = (
		frappe.qb.from_(le)
		.select(Sum(le.debit_in_account_currency - le.credit_in_account_currency))
		.where(
			(le.against_voucher_type == voucher_type)
			& (le.against_voucher == voucher_no)
			& (le.is_cancelled == 0)
			& (le.party.isnotnull())
			& (le.party != "")
		)
	)
	if exclude_own_entries:
		query = query.where((le.voucher_type != voucher_type) | (le.voucher_no != voucher_no))
	if account:
		query = query.where(le.account == account)
	return flt(query.run()[0][0])


def get_paid_amount_from_ledger(voucher_type: str, voucher_no: str, exclude_voucher_types=None) -> float:
	"""Net amount settled against a payable voucher by other vouchers (payments, F&F, ...)."""
	le = frappe.qb.DocType(LEDGER_ENTRY)
	query = (
		frappe.qb.from_(le)
		.select(Sum(le.debit_in_account_currency - le.credit_in_account_currency))
		.where(
			(le.against_voucher_type == voucher_type)
			& (le.against_voucher == voucher_no)
			& (le.is_cancelled == 0)
			& (le.party.isnotnull())
			& (le.party != "")
			& ((le.voucher_type != voucher_type) | (le.voucher_no != voucher_no))
		)
	)
	if exclude_voucher_types:
		query = query.where(le.voucher_type.notin(list(exclude_voucher_types)))
	return flt(query.run()[0][0])


def get_outstanding_amount(voucher_type: str, voucher_no: str, payment_type: str | None = None) -> float:
	"""Amount still to be paid (payment_type Pay) or received (Receive) against a document.

	Documents can define `get_hr_payment_outstanding_amount(payment_type)`; otherwise the ledger
	balance of party entries against the document is used (credit balance = payable).
	"""
	doc = frappe.get_doc(voucher_type, voucher_no)
	if hasattr(doc, "get_hr_payment_outstanding_amount"):
		return flt(doc.get_hr_payment_outstanding_amount(payment_type))

	balance = -get_voucher_balance(voucher_type, voucher_no, exclude_own_entries=False)
	if payment_type == "Receive":
		balance = -balance
	return flt(max(balance, 0) if payment_type else abs(balance))


def get_reference_details(reference_doctype: str, reference_name: str, payment_type: str | None = None) -> dict:
	"""Details an HR Payment needs about a referenced document."""
	doc = frappe.get_doc(reference_doctype, reference_name)
	account = None
	if hasattr(doc, "get_hr_payment_party_account"):
		account = doc.get_hr_payment_party_account()
	else:
		for fieldname in PARTY_ACCOUNT_FIELDS:
			if doc.meta.has_field(fieldname) and doc.get(fieldname):
				account = doc.get(fieldname)
				break

	return frappe._dict(
		{
			"docstatus": doc.docstatus,
			"company": doc.get("company"),
			"party_type": "Employee" if doc.get("employee") else None,
			"party": doc.get("employee"),
			"currency": doc.get("currency") or qcs_hrms.get_company_currency(doc.get("company")),
			"account": account,
			"outstanding_amount": get_outstanding_amount(reference_doctype, reference_name, payment_type),
			"cost_center": doc.get("cost_center"),
		}
	)


def get_default_bank_cash_account(company: str, account_type: str = "Bank") -> str | None:
	"""Default Bank (or Cash) HR Ledger Account of the company."""
	fieldname = "default_cash_account" if account_type == "Cash" else "default_bank_account"
	account = None
	if frappe.get_meta("Company").has_field(fieldname):
		account = frappe.get_cached_value("Company", company, fieldname)
	if account:
		return account

	accounts = frappe.get_all(
		"HR Ledger Account",
		filters={"company": company, "account_type": account_type, "disabled": 0},
		pluck="name",
		order_by="creation asc",
		limit=1,
	)
	return accounts[0] if accounts else None


def get_company_default(company: str, fieldname: str, throw: bool = False):
	value = None
	if frappe.get_meta("Company").has_field(fieldname):
		value = frappe.get_cached_value("Company", company, fieldname)
	if not value and throw:
		frappe.throw(
			_("Please set {0} in Company {1}").format(
				frappe.bold(frappe.unscrub(fieldname)), get_link_to_form("Company", company)
			)
		)
	return value


def make_hr_payment(
	reference_doctype: str,
	reference_name: str,
	payment_type: str = "Pay",
	amount: float | None = None,
	bank_account: str | None = None,
) -> Document:
	"""Return an unsaved HR Payment prefilled for paying (or receiving) against a document."""
	details = get_reference_details(reference_doctype, reference_name, payment_type)
	if details.docstatus != 1:
		frappe.throw(_("{0} {1} must be submitted").format(reference_doctype, reference_name))

	outstanding = flt(details.outstanding_amount)
	allocated = flt(amount) if amount is not None else outstanding

	payment = frappe.new_doc("HR Payment")
	payment.payment_type = payment_type
	payment.company = details.company
	payment.posting_date = nowdate()
	payment.party_type = details.party_type or "Employee"
	payment.party = details.party
	payment.currency = details.currency
	payment.cost_center = details.cost_center
	company_currency = qcs_hrms.get_company_currency(details.company)
	payment.exchange_rate = (
		1 if payment.currency == company_currency else get_rate(payment.currency, company_currency, nowdate())
	)
	bank_account = bank_account or get_default_bank_cash_account(details.company, "Bank") or (
		get_default_bank_cash_account(details.company, "Cash")
	)
	if payment_type == "Pay":
		payment.paid_from, payment.paid_to = bank_account, details.account
	else:
		payment.paid_from, payment.paid_to = details.account, bank_account
	payment.paid_amount = allocated
	payment.append(
		"references",
		{
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"outstanding_amount": outstanding,
			"allocated_amount": allocated,
		},
	)
	payment.remarks = _("{0} against {1} {2}").format(
		_("Payment") if payment_type == "Pay" else _("Receipt"), _(reference_doctype), reference_name
	)
	return payment


def mark_entries_synced(names: list[str], external_reference: str | None = None):
	now = now_datetime()
	for name in names:
		frappe.db.set_value(
			LEDGER_ENTRY,
			name,
			{
				"sync_status": "Synced",
				"external_reference": external_reference,
				"synced_on": now,
				"sync_error": None,
			},
		)
