import click

from qcs_hrms.setup import before_uninstall as remove_fixtures

BUG_REPORT_URL = "https://github.com/QuarkCyberSystems/qcs_hrms/issues/new"


def before_uninstall():
	try:
		print("Removing data created by the QCS HR app...")
		remove_fixtures()

	except Exception as e:
		click.secho(
			"Removing QCS HR fixtures failed due to an error."
			" Please try again or"
			f" report the issue on {BUG_REPORT_URL} if not resolved.",
			fg="bright_red",
		)
		raise e

	click.secho("QCS HR app fixtures have been removed successfully...", fg="green")
