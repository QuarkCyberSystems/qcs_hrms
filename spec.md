# QCS HR: module design specification

QCS HR (`qcs_hrms`) is a standalone fork of Frappe HR v16 (hrms 16.51.0) that runs on **Frappe alone**.
This document specifies how the app is put together: its modules and DocTypes, how it replaces
ERPNext, how the HR ledger works, and the contracts between modules. For installation and
day-to-day use, see [README.md](README.md). For the ledger REST API, see
[docs/ledger_api.md](docs/ledger_api.md).

## 1. Goals and constraints

1. **Runs on Frappe alone.** `required_apps` is empty, and no code imports `erpnext` or `hrms`.
   `check_standalone.py` enforces this.
2. **Upstream DocType names.** DocTypes keep their Frappe HR / ERPNext names (Employee, Company,
   Salary Slip, ...), which keeps future upstream merges simple. Because DocType names are global on a
   site, QCS HR **cannot share a site with `erpnext` or `hrms`**.
3. **No ERP accounting.** Every financial effect of an HR document is written as balanced
   `HR Ledger Entry` rows. External accounting systems read those rows through an API, and post
   payments back through the same API.
4. **Upstream behaviour wherever ERPNext isn't involved.** Leave, attendance, shift, recruitment,
   performance and payroll calculations behave as they do upstream.

## 2. Modules

| Module | Folder | Contents |
|--------|--------|----------|
| Organization | `organization/` | Company, Employee (+ Education, External/Internal Work History), Department, Designation, Branch, Holiday List, Holiday, Employee Group (+ Table), Vehicle, Terms and Conditions, Currency Exchange |
| HR | `hr/` | All upstream HR DocTypes (recruitment, boarding, attendance, shift, leave, expenses, advances, travel, vehicle log, performance, training, F&F) |
| Payroll | `payroll/` | Salary component / structure / assignment, salary slip, payroll entry, payroll period, tax slabs and exemptions, benefits, additional salary, arrears, corrections, gratuity, retention bonus, incentives, salary withholding |
| Ledger | `ledger/` | HR Ledger Account, HR Ledger Entry, HR Payment, HR Payment Reference |
| HR Setup, Tenure, Recruitment, Shift and Attendance, Leaves, Expenses, Performance, Tax and Benefits | workspace folders | Workspaces, sidebars, onboarding, dashboards (no DocTypes) |

Other packages:
- `utils/compat.py` holds replacements for the ERPNext helpers upstream used.
- `controllers/` holds shared controllers: boarding, reminders, and `queries.employee_query`.
- `regional/` holds country-specific payroll logic.
- `api/` holds the whitelisted APIs (including `api/ledger.py`).

## 3. Replacing ERPNext

### 3.1 Concept mapping

| ERPNext concept | QCS HR replacement |
|-----------------|--------------------|
| Company, Employee, Department, Designation, Branch, Holiday List, Employee Group, Vehicle, Terms and Conditions | Ported into **Organization**, trimmed to what HR uses |
| Custom fields hrms added to those DocTypes | Native fields in the DocType JSON (no custom fields at install) |
| `EmployeeMaster` override, hrms Employee/Company/Department JS | Built into the Organization controllers and form scripts |
| `Account` link | `HR Ledger Account` link, same fieldname |
| `Cost Center` link | `Data` field, same fieldname; carried through to ledger entries as a free-text dimension |
| `Bank Account` link | `HR Ledger Account` with account type Bank/Cash |
| `Mode of Payment`, `Supplier` links | `Data` fields, same fieldnames |
| GL Entry, `make_gl_entries`, `AccountsController` | `HR Ledger Entry`, `make_ledger_entries`, `LedgerController` |
| Payment Entry / Journal Entry against HR documents | `HR Payment` |
| Fiscal Year | `Company.fiscal_year_start_month` + `utils.compat.get_fiscal_year` |
| Currency Exchange (ERPNext setup) | Slim `Currency Exchange` in Organization + `utils.compat.get_exchange_rate` |
| Project + Task for onboarding/separation | `ToDo` |
| `erpnext.get_region`, `get_company_currency`, `get_default_company` | `qcs_hrms.get_region`, `qcs_hrms.get_company_currency`, `qcs_hrms.get_default_company` |
| JS `erpnext.*` helpers | `qcs_hrms.get_currency`, `qcs_hrms.queries.employee`, `qcs_hrms.common.*` |

### 3.2 Removed features

- Loans: lending integration, `Salary Slip Loan`, loan columns in reports and print formats.
- Timesheet-based salary: `Salary Slip Timesheet`, hour rate, the timesheet settings.
- Project and Timesheet reports: Employee Hours Utilization, Project Profitability, and two
  timesheet dashboard charts.
- Accounting dimensions, repost accounting ledger, period closing, bank reconciliation queries.
- Expense Claim for Delivery Trip; Project and Task on Expense Claim; Asset Movement on Full and
  Final Asset (assets are entered by hand).
- Telemetry and Frappe Cloud subscription hooks.
- Payroll Settings `process_payroll_accounting_entry_based_on_employee`. Payroll payable is now
  always posted per employee.
- Planned for later: the Vue PWA (`/hrms`) and the roster app (`/hr`), to be ported and redesigned in a later phase. The Python APIs they use (`qcs_hrms.api`, `qcs_hrms.api.roster`) are kept. Translations are not ported yet.

## 4. Organization module

### 4.1 Company
A tree (nested set via `parent_company` / `is_group`), trimmed down to HR needs.

| Group | Fields |
|-------|--------|
| Identity | `company_name`, `abbr`, `country`, `default_currency`, `date_of_establishment`, `tax_id`, `email`, `phone_no`, `website`, `company_logo`, `default_letter_head` |
| HR | `default_holiday_list`, `fiscal_year_start_month` (month name, default January), plus the hrms HR/payroll fields folded in from upstream `setup.py` |
| Ledger defaults (`Link HR Ledger Account`) | `default_payroll_payable_account`, `default_expense_claim_payable_account`, `default_employee_advance_account`, `default_salary_expense_account`, `default_bank_account`, `default_cash_account`, `round_off_account`, `exchange_gain_loss_account` |
| Dimensions (Data) | `cost_center`, `round_off_cost_center` |

What the controller does:
- **validate:** checks the abbreviation, the parent company, that each default account belongs to
  this company, and that the currency isn't changed once ledger entries exist.
- **on_update:**
  - Creates default departments.
  - If any ledger default is missing, calls `qcs_hrms.ledger.setup.create_default_accounts`.
  - On a new company or a country change, runs the regional fixtures and salary components, then
    sets the Expense Claim Type accounts.
  - Sets the global default company if none is set.
- **on_trash:** cleans up HR data linked to the company.

### 4.2 Employee
- **Upstream hrms behaviour built in:** naming follows HR Settings, onboarding validation, approver
  role updates, realtime `publish_update`, job applicant and offer updates, and employee transfer cleanup.
- **Module functions other modules import:** `get_holiday_list_for_employee`, `is_holiday`,
  `get_employee_email(s)`, `get_all_employee_emails`, `InactiveEmployeeStatusError`,
  `validate_employee_role`, `has_upload_permission`, `update_approver_user_roles`,
  `get_timeline_data`, `get_retirement_date`.
- **Holiday list override:** `get_holiday_list_for_employee` delegates to `utils/holiday_list.py`. An
  app can override it with the `employee_holiday_list` hook.
- **Payroll fields:** salary currency, salary mode, bank details and `payroll_cost_center` (Data).

### 4.3 Other masters
- **Department:** a tree, carrying the approver tables and `payroll_cost_center` (Data).
- **Designation:** carries the hrms skill fields.
- **Holiday List:** provides `is_holiday` and `is_half_holiday`, and invalidates the holiday cache on
  update or delete.
- **Vehicle:** `uom` is Data and the Delivery Trip link is removed.
- **Terms and Conditions:** title, disabled, terms, `hr` flag.

## 5. Ledger module

### 5.1 HR Ledger Account
A flat chart of accounts per company. It holds the account numbers of the external system, not a
chart of record.

| Field | Notes |
|-------|-------|
| `account_name`, `company` | Named `"{account_name} - {abbr}"` |
| `account_number` | The external system's account code, used by integrations and the bank remittance report |
| `account_type` | Payable, Receivable, Expense, Income, Asset, Liability, Bank, Cash, Equity, Round Off, Temporary |
| `account_currency`, `disabled`, `description` | |

`ledger/setup.py:create_default_accounts(company)` is idempotent. It creates the accounts below,
reusing any that already exist, and fills only the Company defaults that are empty:

| Account | Type | Company default |
|---------|------|-----------------|
| Payroll Payable | Payable | `default_payroll_payable_account` |
| Expense Claims Payable | Payable | `default_expense_claim_payable_account` |
| Employee Advances | Receivable | `default_employee_advance_account` |
| Salaries | Expense | `default_salary_expense_account` |
| Employee Expenses | Expense | (used for Expense Claim Types) |
| Bank | Bank | `default_bank_account` |
| Cash | Cash | `default_cash_account` |
| Round Off | Round Off | `round_off_account` |

### 5.2 HR Ledger Entry
An immutable, hash-named row. It is created only by `qcs_hrms.ledger.utils`, never through the UI.

| Group | Fields |
|-------|--------|
| Posting | `posting_date`, `company`, `account`, `account_currency`, `debit`, `credit`, `debit_in_account_currency`, `credit_in_account_currency` |
| Party | `party_type`, `party` (normally Employee) |
| Dimension | `cost_center` (Data), `against` |
| Source | `voucher_type`, `voucher_no`, `voucher_detail_no`, `against_voucher_type`, `against_voucher`, `remarks` |
| Grouping | `batch_id`: all rows posted in one call share it, and each batch balances on its own |
| Cancellation | `is_cancelled`, `is_reversal`, `reversal_of` |
| Integration | `sync_status` (Pending / Synced / Failed / Skipped), `external_reference`, `synced_on`, `sync_error` |

Posting rules (`make_ledger_entries(entries, cancel=False, merge_entries=True)`):
1. **Prepare:** each entry gets its company currency amounts and account currency (rate from
   `get_rate`).
2. **Merge:** lines that share account, party, cost center, against voucher and voucher detail
   are merged.
3. **Validate:** all lines must belong to one company, and every account must exist, belong to that
   company and be enabled.
4. **Balance:** if debit and credit differ by more than the rounding allowance
   (`5 / 10^precision`), `LedgerImbalanceError` is raised. A smaller difference is posted to the
   company's `round_off_account`.
5. **Save:** every row is inserted with one shared `batch_id` and `sync_status = Pending`.

Cancelling a voucher (`cancel=True`, which calls `make_reverse_ledger_entries`) never deletes rows:
- Every active row of the voucher gets `is_cancelled = 1`, and a reversal row with debit and credit
  swapped is posted (`is_reversal = 1`, `reversal_of` = the original row).
  The reversals of each original batch get a batch of their own, so every reversal voucher balances
  and has a single sync status.
- **A row that was never synced** (Pending or Failed): the original and its reversal are both marked
  **Skipped**, so the integration ignores the pair.
- **A row that was already Synced:** the reversal is **Pending**, so the integration posts the
  reversal to the external system.

Balance and outstanding helpers:
- `get_party_balance(party_type, party, company, account=None)` returns debit minus credit. A positive
  value means the employee owes the company; a negative value means the company owes the employee.
- `get_outstanding_amount(voucher_type, voucher_no)` returns what is still unpaid on a document.
- `get_default_bank_cash_account(company, account_type)` returns the company's default bank or cash
  account.

### 5.3 LedgerController
The base class for documents that post to the ledger; it replaces ERPNext's `AccountsController`.
- A subclass implements `get_ledger_entries()`, which returns a list of entry dicts.
- `make_ledger_entries(cancel=False)` posts those entries, or reverses them when cancelling.
- `get_ledger_dict(args, account_currency=None, item=None)` fills in company, posting date, voucher
  and remarks; it is the equivalent of ERPNext's `get_gl_dict`.

### 5.4 HR Payment
A submittable document, named `HR-PAY-.YYYY.-`. It replaces Payment Entry and Journal Entry for HR
documents.

| Field | Notes |
|-------|-------|
| `payment_type` | Pay (company to employee) or Receive (employee to company) |
| `party_type`, `party` | Required; one payment per party |
| `paid_from`, `paid_to` | `HR Ledger Account`. On Pay, paid_from is bank/cash and paid_to is the party account; on Receive it is the other way round |
| `paid_amount`, `currency`, `exchange_rate`, `base_paid_amount` | |
| `references` (HR Payment Reference) | `reference_doctype`, `reference_name`, `outstanding_amount`, `allocated_amount` |
| `total_allocated_amount`, `unallocated_amount` | |
| `mode_of_payment`, `reference_no`, `reference_date`, `remarks`, `cost_center` | Free text / Data |

Validation: each reference must be submitted, belong to the same company, party and currency, appear
only once, and have `0 < allocated_amount <= outstanding_amount` (otherwise `HRPaymentAllocationError`).

Ledger effect on submit:
- For a **Pay**, one party line is posted per reference (party account, debit, against that reference),
  plus one for any unallocated amount, balanced by a credit to the bank/cash account.
- A **Receive** posts the mirror image.
- Cancelling reverses the entries.

**Reference handler protocol.** On submit and cancel, `HR Payment` calls each handler registered for a
reference's DocType in the hook `qcs_hrms_payment_reference_handlers`, as
`handler(payment_doc, reference_row, cancel)`. Each owning DocType updates its own paid amount and
status. A referenced DocType may also provide:
- `get_hr_payment_party_account()`: the account the payment settles.
- `get_hr_payment_outstanding_amount(payment_type=None)`: the amount still payable or recoverable.

The candidate fieldnames for the party account are `payable_account`, `advance_account`,
`payroll_payable_account`, `party_account` and `account`.

| Reference DocType | Handler |
|-------------------|---------|
| Salary Slip | `payroll.doctype.salary_slip.salary_slip.update_payment_reference` |
| Payroll Entry | `payroll.doctype.payroll_entry.payroll_entry.update_payment_reference` |
| Gratuity | `payroll.doctype.gratuity.gratuity.update_payment_reference` |
| Expense Claim, Employee Advance, Leave Encashment, Full and Final Statement | handlers in their own modules (`update_payment_for_leave_encashment`, `update_employee_advance_for_payment`, `update_full_and_final_statement_status`, ...) |

## 6. Accounting effects by document

Lines are written as debit (Dr) and credit (Cr). "Party" means `party_type = Employee`. Every posting
balances; rounding differences go to the company round-off account.

| Document | On submit | Settled by |
|----------|-----------|-----------|
| **Expense Claim** | Dr expense account per expense line (from Expense Claim Type), Dr tax `account_head`; Cr `payable_account` (party) for the grand total; Cr employee advance account for allocated advances at the rate they were paid, with any exchange difference inside the same voucher to `exchange_gain_loss_account` (else `round_off_account`). If `is_paid`, also Dr payable / Cr `bank_or_cash_account`. Changing cost centers or tax accounts after submit reverses and re-posts | HR Payment (Pay); `total_amount_reimbursed` is read from the ledger, so F&F settlements count too |
| **Employee Advance** | No posting of its own; the advance is booked by the HR Payment (Dr advance account, party / Cr bank) | HR Payment (Pay). Paid and returned amounts are computed from ledger entries against the advance: credits from an Expense Claim count as claimed, all others (HR Payment Receive, F&F, payroll recovery) as returned. `make_return_entry` returns an HR Payment (Receive). A submitted HR Payment blocks cancelling the advance (the upstream "unlink payment on cancellation" setting has no effect) |
| **Payroll Entry** (accrual, when its salary slips are submitted) | Dr earning component accounts (split by Employee Cost Center, then employee/department payroll cost center, then the entry's cost center); Cr deduction component accounts; Cr employee advance account (party, against Employee Advance) for recoveries, after which the recovered advances are refreshed (`update_advances_for_voucher`, also on cancel); Cr `payroll_payable_account` (party, against each Salary Slip) for net pay; employer contributions Dr expense / Cr liability | "Make HR Payment" creates one draft HR Payment per employee, with one reference per Salary Slip. A slip is marked paid (`hr_payment` set, withholding released) when fully paid; the Payroll Entry becomes **Paid** when all slips are |
| **Gratuity** | Dr `expense_account`; Cr `payable_account` (party, against Gratuity) | HR Payment; paid amount and status are recomputed from submitted payments |
| **Leave Encashment** | Only when `pay_via_payment_entry` is set: Dr `expense_account`; Cr `payable_account` (party) | HR Payment; paid amount comes from the ledger |
| **Full and Final Statement** | A settlement voucher (new; upstream posts nothing): Dr each payable row and Cr each receivable / asset recovery row against its source document; the net goes to `payable_account` ("Settlement Account", defaulting to the company payroll payable account) against the statement. Linked Expense Claims, Advances and Leave Encashments are refreshed from the ledger; Gratuity `paid_amount` is set directly, as upstream | HR Payment (Pay or Receive, by the sign of `net_payable_amount`); status becomes Paid when settled |
| **Salary Withholding** | No posting of its own | Released by the Salary Slip handler when the withheld slip is paid |

Cancelling any of these reverses its entries (see 5.2). A Payroll Entry cannot be cancelled while it
has submitted HR Payments; its draft HR Payments are deleted on cancel, its accrual is reversed, and then
its salary slips are cancelled and deleted (cancelled ledger entries against a slip don't block the deletion).

## 7. Integration API (`qcs_hrms.api.ledger`)

These are whitelisted methods at `/api/method/qcs_hrms.api.ledger.<method>`. They check permissions
(`HR Ledger Entry` read, `HR Payment` create/submit, read access on each referenced document) and build
queries with `frappe.qb`. The page size is capped at 1000.

| Method | Contract |
|--------|----------|
| `get_accounts(company=None, include_disabled=0)` | HR Ledger Accounts, with `account_number` |
| `get_ledger_entries(...)` | Filters: company, from_date, to_date, voucher_type, sync_status, modified_since; paged with start / page_length |
| `get_vouchers(...)` | One balanced, journal-shaped voucher per `batch_id`, with header and lines |
| `mark_synced(entries=None, vouchers=None, external_reference=None)` | Marks entries, or whole batches, Synced |
| `mark_sync_failed(...)` | Records `sync_error` and sets the status to Failed |
| `get_party_balance(...)` | Same sign convention as 5.2 |
| `get_outstanding_documents(employee, company=None)` | Unpaid claims, advances, payroll slips, gratuity, encashments and F&F statements |
| `record_payment(...)` (POST) | Creates an HR Payment and submits it by default. Takes either one `reference_doctype` + `reference_name` (+ `amount`), or a `references` list together with `party` |

Integration flow:
1. Poll `get_vouchers(sync_status="Pending")`, or set up a Frappe Webhook on `HR Ledger Entry`.
2. Post each voucher to the external system.
3. Call `mark_synced` (or `mark_sync_failed`).
4. When the external system pays or receives money, call `record_payment`. This updates the HR
   documents and posts the settlement entries, which the next sync picks up.

## 8. HR module design changes

### Onboarding and separation use ToDos instead of Project and Task
- On submit, and on update after submit for new rows, each Employee Boarding Activity creates one
  `ToDo` per assignee:
  - The assignees are the activity user plus the enabled users holding the activity role
    (Administrator excluded).
  - If no assignee can be resolved, one unassigned ToDo is created with `role` set.
- **Due date:** the activity's end date, from `begin_on` and `duration`, pushed past holidays.
- **Access:** assignees get an assignment notification, plus read access to the document when sharing
  is enabled.
- **Linking:** `Employee Boarding Activity.task` is `Link ToDo`, and the hidden `todos` field holds
  every ToDo created for the row.
- **Status:**
  - An activity is done when any of its ToDos is Closed, or all of them are Cancelled.
  - The boarding status (Pending, In Process or Completed) is weighted by `task_weight` when weights
    are set.
  - It is recomputed by the ToDo `on_update` hook (`update_boarding_status_from_todo`).
  - Closing or reopening one assignee's ToDo does the same to the others for that activity.
- **Cancellation:** cancelling the document sets its open ToDos to Cancelled.

### Other changes
- Training Event and Training Program: `supplier` is Data.
- Leave Application calendar: the default company comes from `qcs_hrms.get_default_company`.
- Permissions widened from upstream: HR Manager and HR User have full access (including submit, cancel
  and amend) to Employee Advance and Vehicle Log, and can open the Employee Advance Summary and Vehicle
  Expenses reports. The ESS user type setup copies standard permissions into Custom DocPerms, which then
  replace them. On sites installed before this change, the patch
  `v0_0.grant_hr_roles_on_advance_and_vehicle_log` adds the HR roles to those Custom DocPerms.

## 9. Payroll module design changes

- These fields now link to `HR Ledger Account`:
  - Salary Component Account: `account`, `liability_account`
  - Salary Structure: `payment_account`
  - Salary Structure Assignment and Bulk Assignment: `payroll_payable_account`
  - Payroll Entry: `payment_account`, `payroll_payable_account`
  - Gratuity: `expense_account`, `payable_account`
- Salary Slip: `journal_entry` is replaced by `hr_payment` (read-only) and `accrual_posted` (hidden).
  Salary Withholding Cycle: `journal_entry` is replaced by `hr_payment`.
- Payroll Entry: the `project` and `bank_account` fields are removed, `cost_center` is optional Data,
  and `status` gains **Paid**.
- `get_month_details(year_start_date, month)` takes the fiscal year start date.
- `qcs_hrms.payroll.utils.get_exchange_rate` (whitelisted) serves the forms.
- Regional (India and others): the logic is unchanged and creates no accounts. India custom fields
  anchor on Company `default_payroll_payable_account` and Employee `payroll_cost_center`, so those
  fieldnames must not change.

## 10. App wiring (`hooks.py`)

- **No `required_apps`.** The bundles have unique names (`qcs_hrms.bundle.js`,
  `qcs_performance.bundle.js`, `qcs_hierarchy-chart.bundle.js`, `qcs_interview.bundle.js`), so their
  assets cannot collide with an `hrms` checkout in the same bench.
- `standard_queries = {"Employee": "qcs_hrms.controllers.queries.employee_query"}`.
- **doc_events:**
  - User: approver roles.
  - Holiday List: cache invalidation.
  - Employee: onboarding, approvers, realtime updates.
  - ToDo: boarding status.
  - No hooks on ERPNext DocTypes.
- `qcs_hrms_payment_reference_handlers`: see 5.4.
- **Kept from upstream:** scheduler events (reminders, auto attendance, shift creation, leave
  expiry, earned leave, encashment, job opening expiry), calendars, the job opening website generator,
  `regional_overrides` and `global_search_doctypes`.
- **Setup:**
  - `after_install` creates fixtures, notifications, HR defaults, role profiles and ESS user types. It is
    idempotent and only fills blank values.
  - The setup wizard adds a Company step (name, abbreviation, fiscal year start month).
  - On a site whose wizard is already complete, the first Company is created by hand.

## 11. Contracts between modules

| Provider | Contract |
|----------|----------|
| `qcs_hrms` (package) | `get_region`, `allow_regional`, `refetch_resource`, `get_default_company`, `get_default_currency`, `get_company_currency` |
| `qcs_hrms.utils.compat` | `validate_status`, `build_qb_match_conditions`, `get_fiscal_year(date=None, company=None, as_dict=False)` (tuple `(name, start, end)` or dict), `get_fiscal_year_dates`, `get_fiscal_year_for_date` (whitelisted), `daterange`, `set_by_naming_series`, `get_exchange_rate(from_currency, to_currency, transaction_date=None)`, `delete_events` |
| `qcs_hrms.controllers.queries` | `employee_query` |
| JS bundle | `qcs_hrms.get_currency(company)`, `qcs_hrms.queries.employee`, `qcs_hrms.common.{get_tree_options, get_tree_default, get_fiscal_year, copy_value_in_all_rows, get_terms, map_current_doc}` |
| `qcs_hrms.ledger.utils` | `make_ledger_entries`, `make_reverse_ledger_entries`, `LedgerController`, `LedgerImbalanceError`, `get_party_balance`, `get_outstanding_amount`, `get_default_bank_cash_account`, `make_hr_payment`, `mark_entries_synced` |
| `qcs_hrms.ledger.setup` | `create_default_accounts(company)` |
| `qcs_hrms.tests.utils` | `HRMSTestSuite` (rollback, `set_user`, `change_settings`), and bootstrap test data: the `_Test Company` tree, departments, `_T-Employee-0000x` |
| `organization.doctype.employee.test_employee` | `make_employee` |

## 12. Quality gates

- `env/bin/python apps/qcs_hrms/check_standalone.py [path ...]` must report 0 problems. It fails on:
  - invalid DocType JSON
  - a module missing from `modules.txt`
  - a Link, Table or Table MultiSelect field whose target DocType exists in neither Frappe nor this app
  - any reference to `erpnext` or `hrms.` in non-test code
- `bench --site <site> run-tests --app qcs_hrms` runs on a site with only `frappe` and `qcs_hrms`.
- Pre-commit: ruff, eslint, prettier.
