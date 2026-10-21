# HR Ledger integration API

qcs_hrms has no accounting module of its own. Every financial effect of an HR document is
written as balanced **HR Ledger Entry** rows. An external accounting system (ERPNext, Odoo, Xero,
SAP, ...) reads those rows and posts them as journals.

| DocType | Purpose |
|---|---|
| HR Ledger Account | Account per company. `account_number` holds the code of the matching account in the external system. |
| HR Ledger Entry | One immutable debit/credit line. Entries posted together share a `batch_id`, which works as one balanced journal. |
| HR Payment | A payment made (Pay) or received (Receive) for an employee, allocated against Expense Claims, Employee Advances, Leave Encashments, Full and Final Statements, Gratuity, payroll, and so on. |

Documents that post entries: Expense Claim, Leave Encashment (when paid via HR Payment), Full and
Final Statement, HR Payment, plus the payroll documents.

Cancelling a document marks its entries `is_cancelled = 1` and posts reversal entries
(`is_reversal = 1`), one new batch for each original batch:

- If the original entries were already **Synced**, the reversal is **Pending**, so the
  integration posts the reversal.
- If the original entries were never synced (Pending or Failed), both the originals and the
  reversal become **Skipped**.

`sync_status` takes one of these values: `Pending`, `Synced`, `Failed` or `Skipped`.

All methods live at `/api/method/qcs_hrms.api.ledger.<method>` and use normal Frappe
authentication. Use an API key and secret of a user with the *Accounts User*, *Accounts Manager*
or *HR Manager* role. User permissions on Company are respected.

```bash
AUTH="Authorization: token <api_key>:<api_secret>"
SITE=https://hr.example.com
```

## Endpoints

### get_accounts
Lists accounts so you can map each one to the external chart of accounts.
```bash
curl -s -H "$AUTH" "$SITE/api/method/qcs_hrms.api.ledger.get_accounts?company=ACME"
```
Returns a list of `name, account_name, account_number, company, account_type, account_currency, disabled`.

### get_vouchers (recommended for syncing)
Returns balanced journals: one object per posting batch.
```bash
curl -s -H "$AUTH" "$SITE/api/method/qcs_hrms.api.ledger.get_vouchers?company=ACME&sync_status=Pending&page_length=100"
```
Filters:
- `company`
- `from_date` and `to_date` (posting date)
- `voucher_type`
- `sync_status`: default `Pending`. Accepts a JSON list or a comma-separated string.
- `modified_since`
- `start` and `page_length`: maximum 1000

The response looks like this:
```json
{"message": {"vouchers": [{
  "batch_id": "a1b2c3d4e5f6", "company": "ACME", "currency": "AED",
  "posting_date": "2026-10-20", "voucher_type": "Expense Claim", "voucher_no": "HR-EXP-2026-00001",
  "is_reversal": 0, "total_debit": 218.0, "total_credit": 218.0, "is_balanced": true,
  "sync_status": "Pending",
  "lines": [{"name": "...", "account": "Travel Expenses - AC", "account_number": "6100",
             "debit": 200.0, "credit": 0.0, "party_type": null, "party": null, "cost_center": "Main", ...},
            {"account": "Expense Claims Payable - AC", "account_number": "2110", "credit": 218.0,
             "party_type": "Employee", "party": "HR-EMP-00001",
             "against_voucher_type": "Expense Claim", "against_voucher": "HR-EXP-2026-00001", ...}]
}], "start": 0, "page_length": 100, "has_more": false}}
```

### get_ledger_entries
Returns ledger lines as a flat list, with the same filters plus `voucher_no`, `account`,
`party_type`, `party` and `include_cancelled` (default 1). Results come back in pages.
```bash
curl -s -H "$AUTH" "$SITE/api/method/qcs_hrms.api.ledger.get_ledger_entries?company=ACME&from_date=2026-10-01&to_date=2026-10-31&start=0&page_length=500"
```

### mark_synced (POST)
Pass `vouchers` (a list of `batch_id`) or `entries` (a list of HR Ledger Entry names), plus an
optional `external_reference`, such as the journal number in the external system.
```bash
curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"vouchers": ["a1b2c3d4e5f6"], "external_reference": "JV-2026-0042"}' \
  "$SITE/api/method/qcs_hrms.api.ledger.mark_synced"
```

### mark_sync_failed (POST)
Records why a sync failed. Failed entries come back when you query with `sync_status=Failed`.
```bash
curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"vouchers": ["a1b2c3d4e5f6"], "error": "Account 6100 not mapped"}' \
  "$SITE/api/method/qcs_hrms.api.ledger.mark_sync_failed"
```

### get_party_balance
Returns debit minus credit in company currency. A positive balance means the employee owes the
company. A negative balance means the company owes the employee.
```bash
curl -s -H "$AUTH" "$SITE/api/method/qcs_hrms.api.ledger.get_party_balance?party=HR-EMP-00001&company=ACME"
```

### get_outstanding_documents
Lists submitted documents of one employee that still have something to pay (`payment_type=Pay`)
or to recover (`Receive`).
```bash
curl -s -H "$AUTH" "$SITE/api/method/qcs_hrms.api.ledger.get_outstanding_documents?employee=HR-EMP-00001&company=ACME"
```

### record_payment (POST)
Records a payment made or received outside qcs_hrms. It creates and submits an HR Payment, which
posts ledger entries and updates the paid status of the referenced documents.
```bash
# reimburse an expense claim (amount defaults to the outstanding amount)
curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"reference_doctype": "Expense Claim", "reference_name": "HR-EXP-2026-00001",
       "amount": 218, "reference_no": "TRF-99812", "reference_date": "2026-10-21",
       "bank_account": "Bank - AC", "mode_of_payment": "Bank Transfer"}' \
  "$SITE/api/method/qcs_hrms.api.ledger.record_payment"

# one payment against several documents of the same employee
curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"party": "HR-EMP-00001", "amount": 1500, "references": [
        {"reference_doctype": "Expense Claim", "reference_name": "HR-EXP-2026-00001", "allocated_amount": 500},
        {"reference_doctype": "Leave Encashment", "reference_name": "HR-ENC-2026-00003", "allocated_amount": 1000}]}' \
  "$SITE/api/method/qcs_hrms.api.ledger.record_payment"

# recover an unclaimed advance
curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"payment_type": "Receive", "reference_doctype": "Employee Advance", "reference_name": "HR-EAD-2026-00002"}' \
  "$SITE/api/method/qcs_hrms.api.ledger.record_payment"
```
Optional fields:
- `posting_date`
- `paid_from` and `paid_to`
- `currency` and `exchange_rate`
- `remarks`
- `submit`: set it to 0 to keep the payment as a draft

## Suggested integration flow

1. **Map accounts once.** Set `account_number` on every HR Ledger Account to the code of the
   matching account in your system. Read them with `get_accounts`.
2. **Push the journals.** Either:
   - poll `get_vouchers?sync_status=Pending` on a schedule, or
   - create a Frappe **Webhook** on *HR Ledger Entry* (event `after_insert`) that notifies your
     middleware, which then calls `get_vouchers` for the voucher.

   Post each voucher as one journal, using the `account_number` and `party` of each line.
3. **Acknowledge.** Call `mark_synced` with the `batch_id` values and your journal reference, or
   `mark_sync_failed` with the error. Retry failed vouchers by querying `sync_status=Failed`.
   Cancelled documents produce reversal vouchers, which go through the same flow.
4. **Payments.** You can record payments in either system:
   - Pay employees inside qcs_hrms: use the *Record Payment* button on the document, which creates
     an HR Payment. Its ledger entries are synced like any other voucher.
   - Pay employees outside qcs_hrms: call `record_payment` so the HR document is marked paid. Its
     ledger entries debit the employee payable and credit the bank account. If your system already
     booked that payment, mark the HR Payment voucher as synced right away.
