### QCS HR

Standalone fork of [Frappe HR](https://github.com/frappe/hrms) that runs on **Frappe alone**, with no ERPNext.

Upstream Frappe HR requires ERPNext for its masters (Company, Employee, Department, ...) and posts
accounting through ERPNext (GL Entry, Journal Entry, Payment Entry). QCS HR brings the masters into the
app and replaces the accounting integration with its own **ledger**, which any external accounting system
can read and post back to through a REST API.

Based on Frappe HR `version-16` (16.51.0), for Frappe v16. The module design is specified in
[spec.md](spec.md).

### What is included

| Area | Contents |
|------|----------|
| Organization | Company, Employee, Department, Designation, Branch, Holiday List, Employee Group, Vehicle, Terms and Conditions (ported from ERPNext, trimmed to what HR uses) |
| HR | Recruitment, onboarding and separation, shifts and attendance, leaves, expenses and advances, performance, training, travel |
| Payroll | Salary components and structures, salary slips, payroll entry, tax slabs and exemptions, benefits, gratuity, arrears, salary withholding |
| Ledger | HR Ledger Account, HR Ledger Entry, HR Payment, plus the integration API |

All DocTypes keep their upstream names. This means QCS HR **cannot be installed on the same site as
`erpnext` or `hrms`**.

### Differences from upstream Frappe HR

- **No ERPNext dependency.** `required_apps` is empty. Organization masters live in the `Organization`
  module, and the Employee, Company and Department behaviour that hrms used to add through overrides and
  custom fields is now built into those DocTypes.
- **Accounting goes to the ledger instead of ERPNext:**
  - `Account` links point to `HR Ledger Account`, a simple chart that holds the external system's account
    number.
  - Cost centers and mode of payment are plain text fields, carried through to ledger entries.
  - Payroll Entry, Expense Claim, Employee Advance, Gratuity, Leave Encashment and Full and Final
    Statement post balanced `HR Ledger Entry` rows on submit, and post reversals on cancel.
  - Payments and recoveries are recorded with `HR Payment`, which replaces Payment Entry and Journal
    Entry. A payment updates the paid amount and status of the documents it references.
- **Onboarding and separation activities create ToDos** instead of a Project with Tasks. Boarding status
  follows those ToDos.
- **Removed:** loans (lending integration), timesheet-based salary, Project and Timesheet reports,
  accounting dimensions, bank reconciliation, telemetry and Frappe Cloud subscription hooks.
- **Planned for later:** the Vue PWA (`/hrms`) and the roster app (`/hr`), to be ported and redesigned in a later phase. Translations are not ported yet.

### Ledger integration API

Whitelisted methods under `qcs_hrms.api.ledger`, called as `/api/method/qcs_hrms.api.ledger.<method>`:

| Method | Purpose |
|--------|---------|
| `get_accounts` | HR Ledger Accounts of a company |
| `get_ledger_entries` | Filtered, paged ledger entries (company, dates, voucher type, sync status, modified since) |
| `get_vouchers` | Entries grouped into balanced, journal-shaped vouchers, ready to post elsewhere |
| `mark_synced` / `mark_sync_failed` | Record the external reference, or the error, after posting |
| `get_party_balance` | An employee's balance, overall or for one account |
| `get_outstanding_documents` | An employee's unpaid claims, advances and payroll payables |
| `record_payment` | Create and submit an HR Payment against one or more documents |

A typical flow:
1. Poll `get_vouchers` for `Pending` vouchers, or set up a Frappe Webhook on `HR Ledger Entry`.
2. Post the vouchers to the accounting system.
3. Call `mark_synced`.
4. When the accounting system makes a payment, call `record_payment` so the HR documents show as paid.

See [docs/ledger_api.md](docs/ledger_api.md) for parameters and examples.

### Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench --site $SITE install-app qcs_hrms
```

The site must not have `erpnext` or `hrms` installed. On a new site, the setup wizard has a step that
creates the first Company. On a site whose setup wizard has already been completed, create the first
Company by hand after installing (**Company > New**). When a Company is saved without ledger account
defaults, a minimal set of HR Ledger Accounts is created and assigned to it, along with default
departments and HR fixtures.

### Development

Check that the app has no ERPNext or hrms coupling, and that every Link field points to a DocType
that exists in Frappe or this app:

```bash
# from the bench root; optional path prefixes are relative to qcs_hrms/qcs_hrms
env/bin/python apps/qcs_hrms/check_standalone.py
env/bin/python apps/qcs_hrms/check_standalone.py payroll/ ledger/
```

Run the tests:

```bash
bench --site $SITE set-config allow_tests true
bench --site $SITE run-tests --app qcs_hrms
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/qcs_hrms
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

gpl-3.0. Derived from [Frappe HR](https://github.com/frappe/hrms) (GPL-3.0) and parts of
[ERPNext](https://github.com/frappe/erpnext) (GPL-3.0).
