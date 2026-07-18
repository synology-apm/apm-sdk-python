"""synology-apm-cli — Typer root entry point."""
from __future__ import annotations

import typer

from synology_apm.cli.commands import activity as activity_commands
from synology_apm.cli.commands import config as config_commands
from synology_apm.cli.commands import infra as infra_commands
from synology_apm.cli.commands import log as log_commands
from synology_apm.cli.commands import m365 as m365_commands
from synology_apm.cli.commands import machine as machine_commands
from synology_apm.cli.commands import plan as plan_commands
from synology_apm.cli.commands import saas as saas_commands

app = typer.Typer(
    name="synology-apm-cli",
    help="""Synology ActiveProtect Manager CLI.

\b
Connection settings (priority: high → low):
  1. CLI flags     --profile / --host / --username / --password / --no-verify-ssl
  2. Env vars      APM_PROFILE, APM_HOST, APM_USERNAME, APM_PASSWORD, APM_NO_VERIFY_SSL
  3. Config file   ~/.config/synology-apm/config.toml (set with synology-apm-cli config set)

\b
Examples:
  synology-apm-cli config set
  synology-apm-cli --host apm.corp.com --username admin machine list
  synology-apm-cli --host apm.corp.com:10443 --username admin machine list

\b
Output format (-o / --output):
  table   Rich table (default)
  json    JSON; pipe to jq: synology-apm-cli machine list -o json | jq '.[].name'
  yaml    YAML format

\b
Exit codes:
  0  Success
  1  General error (API error, invalid argument)
  2  Authentication failure (bad credentials, session expired)
  3  Connection failure (host unreachable, TLS error)
  4  User cancelled (answered no to a confirmation prompt)
  5  Feature not supported by this APM version
""",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback()
def _root_callback(
    ctx: typer.Context,
    host: str | None = typer.Option(None, "--host", help="APM hostname or IP, supports host:port (env: APM_HOST)"),
    username: str | None = typer.Option(None, "--username", "-u", help="Login account (env: APM_USERNAME)"),
    password: str | None = typer.Option(None, "--password", "-p", help="Password (env: APM_PASSWORD)"),
    profile: str | None = typer.Option(None, "--profile", help="Config profile name (env: APM_PROFILE)"),
    no_verify_ssl: bool | None = typer.Option(
        None, "--no-verify-ssl", help="Skip SSL verification (env: APM_NO_VERIFY_SSL)"
    ),
    no_input: bool = typer.Option(
        False, "--no-input",
        help="Disable all interactive prompts; exit with an error if required input is missing.",
    ),
    debug: bool = typer.Option(False, "--debug", hidden=True, help="Print API requests/responses to stderr."),
) -> None:
    ctx.ensure_object(dict)
    ctx.obj.update({
        "host": host,
        "username": username,
        "password": password,
        "profile": profile,
        "no_verify_ssl": no_verify_ssl,
        "no_input": no_input,
        "debug": debug,
    })


app.add_typer(config_commands.app, name="config")
app.add_typer(machine_commands.app, name="machine")
app.add_typer(saas_commands.app, name="saas")
app.add_typer(m365_commands.app, name="m365")
app.add_typer(plan_commands.app, name="plan")
app.add_typer(activity_commands.app, name="activity")
app.add_typer(infra_commands.app, name="infra")
app.add_typer(log_commands.app, name="log")


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
