# Copyright (c) 2026, Quark Cyber System FZC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class HRLedgerEntry(Document):
	"""Immutable ledger line. Created only through `qcs_hrms.ledger.utils.make_ledger_entries`."""

	def validate(self):
		if self.is_new() and not self.flags.from_ledger_utils:
			frappe.throw(_("HR Ledger Entries can only be created by the system"), frappe.PermissionError)
		if not self.is_new():
			frappe.throw(_("HR Ledger Entries can not be edited"), frappe.PermissionError)


def on_doctype_update():
	frappe.db.add_index("HR Ledger Entry", ["voucher_type", "voucher_no"])
	frappe.db.add_index("HR Ledger Entry", ["against_voucher_type", "against_voucher"])
	frappe.db.add_index("HR Ledger Entry", ["party_type", "party"])
	frappe.db.add_index("HR Ledger Entry", ["company", "posting_date"])
