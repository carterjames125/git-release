"""rich-click group + `release` subcommand. Argument parsing and wiring only -
business logic (currently just building a dry-run summary) is a plain function
callable without Click, per the project's layering rule.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import rich_click as click
from dotenv import load_dotenv
from loguru import logger

from gitlab_release import changelog, config, gitlab_client
from gitlab_release import logging as log_setup
from gitlab_release.errors import ConfigError, InternalError, ReleaseError


@dataclass(frozen=True)
class RunContext:
    verbose: bool
    quiet: bool
    json_output: bool
    level: str


def handle_errors(func: Callable[..., None]) -> Callable[..., None]:
    """The single top-level error handler, applied to every command. Translates
    ReleaseError subclasses (and any truly unexpected exception, wrapped as
    InternalError) into a clean stderr message + correct exit code, with a traceback
    only when --verbose was passed."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        ctx = click.get_current_context()
        assert ctx is not None
        run_ctx: RunContext = ctx.obj
        try:
            func(*args, **kwargs)
        except ReleaseError as exc:
            _fail(exc, verbose=run_ctx.verbose)
        except Exception as exc:  # noqa: BLE001 - safety net for genuine bugs
            _fail(InternalError(f"Unexpected error: {exc}"), verbose=run_ctx.verbose)

    return wrapper


def _fail(exc: ReleaseError, *, verbose: bool) -> NoReturn:
    if verbose:
        logger.opt(exception=True).debug("Failing with {}", type(exc).__name__)
    click.echo(click.style(f"Error: {exc.message}", fg="red"), err=True)
    raise SystemExit(exc.exit_code)


@click.group()
@click.version_option(package_name="git-release")
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    envvar="RELEASE_VERBOSE",
    help="Debug-level logging with stack traces.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    envvar="RELEASE_QUIET",
    help="Warning-level logging only.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    envvar="RELEASE_JSON",
    help="Emit a machine-readable JSON summary on stdout.",
)
@click.pass_context
def cli(ctx: click.Context, verbose: bool, quiet: bool, json_output: bool) -> None:
    """gitlab-release: automate the release step of a GitLab CI pipeline."""
    if verbose and quiet:
        raise click.UsageError("--verbose and --quiet are mutually exclusive.")
    level = log_setup.resolve_level(verbose=verbose, quiet=quiet)
    log_setup.configure_logging(level=level, json_output=json_output, verbose=verbose)
    ctx.obj = RunContext(verbose=verbose, quiet=quiet, json_output=json_output, level=level)


@cli.command()
@click.option(
    "--gitlab-url",
    envvar="GITLAB_URL",
    default=None,
    help="GitLab instance URL. Falls back to CI_SERVER_URL.",
)
@click.option(
    "--project-id",
    envvar="GITLAB_PROJECT_ID",
    default=None,
    help="Project ID or path. Falls back to CI_PROJECT_ID.",
)
@click.option(
    "--token",
    envvar="GITLAB_TOKEN",
    default=None,
    help="API token. Falls back to CI_JOB_TOKEN (cannot create tags).",
)
@click.option(
    "--tag",
    envvar="RELEASE_TAG",
    default=None,
    help="Release tag. Falls back to CI_COMMIT_TAG.",
)
@click.option(
    "--ca-bundle",
    envvar="REQUESTS_CA_BUNDLE",
    default=None,
    type=click.Path(path_type=Path, dir_okay=False),
    help="CA bundle for self-hosted instances.",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=True,
    envvar="RELEASE_DRY_RUN",
    help="Preview only; --no-dry-run is reserved for a future release.",
)
@click.option(
    "--template",
    "template_path",
    envvar="RELEASE_TEMPLATE",
    default=None,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Override the bundled changelog template.",
)
@click.option(
    "--notify/--no-notify",
    default=False,
    envvar="RELEASE_NOTIFY",
    help="Preview an SMTP notification (sending is not implemented yet).",
)
@click.option("--smtp-host", envvar="SMTP_HOST", default=None)
@click.option("--smtp-port", envvar="SMTP_PORT", default=None, type=int)
@click.option("--smtp-user", envvar="SMTP_USER", default=None)
@click.option("--smtp-password", envvar="SMTP_PASSWORD", default=None)
@click.option("--smtp-from", envvar="SMTP_FROM", default=None)
@click.option("--smtp-to", envvar="SMTP_TO", default=None)
@click.option(
    "--smtp-starttls/--no-smtp-starttls",
    envvar="SMTP_STARTTLS",
    default=False,
)
@handle_errors
def release(
    gitlab_url: str | None,
    project_id: str | None,
    token: str | None,
    tag: str | None,
    ca_bundle: Path | None,
    dry_run: bool,
    template_path: Path | None,
    notify: bool,
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_user: str | None,
    smtp_password: str | None,
    smtp_from: str | None,
    smtp_to: str | None,
    smtp_starttls: bool,
) -> None:
    """Show what a release would do, without creating anything."""
    ctx = click.get_current_context()
    assert ctx is not None
    run_ctx: RunContext = ctx.obj

    settings = config.load_settings(
        gitlab_url=gitlab_url,
        project_id=project_id,
        token=token,
        tag=tag,
        ca_bundle=ca_bundle,
    )
    secrets = list(settings.secret_values())

    notify_settings: config.NotifySettings | None = None
    if notify:
        notify_settings = config.load_notify_settings(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            smtp_from=smtp_from,
            smtp_to=smtp_to,
            smtp_starttls=smtp_starttls,
        )
        secrets.extend(notify_settings.secret_values())

    # Re-configure the sink now that secrets are known, before any further code runs.
    log_setup.configure_logging(
        level=run_ctx.level,
        json_output=run_ctx.json_output,
        verbose=run_ctx.verbose,
        secrets=secrets,
    )

    if not dry_run:
        raise ConfigError(
            "--no-dry-run is not supported yet: release creation, artifact upload, "
            "and notification sending are not implemented in this build. "
            "Only --dry-run is available."
        )

    client = gitlab_client.GitlabClient(
        url=settings.gitlab_url,
        project_id=settings.project_id,
        token=settings.token,
        is_job_token=settings.is_job_token,
        ca_bundle=settings.ca_bundle,
    )
    context = changelog.build_context(client, settings)
    changelog_text = changelog.render(context, template_path=template_path)

    _run_release(
        settings=settings,
        changelog_text=changelog_text,
        notify_settings=notify_settings,
        json_output=run_ctx.json_output,
    )


def _run_release(
    *,
    settings: config.Settings,
    changelog_text: str,
    notify_settings: config.NotifySettings | None,
    json_output: bool,
) -> None:
    """Business logic seam - no click objects below this line. Currently prints a
    projection of the validated config plus a real, GitLab-sourced changelog. Does not
    call notify.send_notification: dry-run is still the only supported mode, so the
    notification is always previewed, never sent.
    """
    logger.debug("Building dry-run summary for tag={}", settings.tag)
    subject = f"Release {settings.tag}"
    summary: dict[str, Any] = {
        "dry_run": True,
        "gitlab_url": settings.gitlab_url,
        "project_id": settings.project_id,
        "tag": settings.tag,
        "ref": settings.ref,
        "changelog": changelog_text,
        # settings.token is intentionally never placed into this dict.
    }
    if notify_settings is not None:
        summary["notify_preview"] = {"to": notify_settings.smtp_to, "subject": subject}

    if json_output:
        click.echo(json.dumps(summary))
        return

    click.echo(
        f"[dry-run] Would create release for tag {settings.tag!r} at ref "
        f"{settings.ref!r} on project {settings.project_id!r} "
        f"({settings.gitlab_url}). No changes made."
    )
    click.echo("\n--- Changelog preview ---")
    click.echo(changelog_text)
    if notify_settings is not None:
        click.echo(
            f"[dry-run] Would send notification to {notify_settings.smtp_to} "
            f'with subject "{subject}". No email sent.'
        )


def main() -> None:
    """Console-script (`gitlab-release`) and `python -m gitlab_release` entry point.
    load_dotenv MUST run here, before `cli()` - Click resolves envvar-backed option
    defaults at parse time, so calling load_dotenv from inside a command callback
    would be too late for `.env` values to affect option resolution."""
    load_dotenv(override=False)
    cli()


if __name__ == "__main__":
    main()
