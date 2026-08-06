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

from gitlab_release import config, gitlab_client
from gitlab_release import logging as log_setup
from gitlab_release import release as release_flow
from gitlab_release.errors import InternalError, ReleaseError


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
    help="Preview only; --no-dry-run creates the tag, release, and uploads artifacts.",
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
    "--source-path",
    envvar="RELEASE_SOURCE_PATH",
    default=None,
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Directory of build artifacts to upload. Omit to skip artifact upload.",
)
@click.option(
    "--artifact-pattern",
    envvar="RELEASE_ARTIFACT_PATTERN",
    default="*",
    help="Glob pattern for artifacts, relative to --source-path.",
)
@click.option(
    "--package-name",
    envvar="RELEASE_PACKAGE_NAME",
    default=None,
    help="Generic package name. Defaults to the project's path slug.",
)
@click.option(
    "--release-name",
    envvar="RELEASE_NAME",
    default=None,
    help="GitLab release name. Defaults to the tag.",
)
@click.option(
    "--if-exists",
    envvar="RELEASE_IF_EXISTS",
    default="fail",
    type=click.Choice(["fail", "skip", "update"]),
    help="Behavior when the release or a package file already exists. Tag creation "
    "always fails if the tag already exists, regardless of this setting.",
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
    source_path: Path | None,
    artifact_pattern: str,
    package_name: str | None,
    release_name: str | None,
    if_exists: str,
    notify: bool,
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_user: str | None,
    smtp_password: str | None,
    smtp_from: str | None,
    smtp_to: str | None,
    smtp_starttls: bool,
) -> None:
    """Create a release: tag, upload artifacts, and publish it. Preview-only unless
    --no-dry-run is passed."""
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

    client = gitlab_client.GitlabClient(
        url=settings.gitlab_url,
        project_id=settings.project_id,
        token=settings.token,
        is_job_token=settings.is_job_token,
        ca_bundle=settings.ca_bundle,
    )

    artifact_settings: config.ArtifactSettings | None = None
    if source_path is not None:
        resolved_package_name = package_name or client.project_path()
        artifact_settings = config.load_artifact_settings(
            source_path=source_path,
            pattern=artifact_pattern,
            package_name=resolved_package_name,
        )

    result = release_flow.execute(
        client,
        settings,
        dry_run=dry_run,
        artifact_settings=artifact_settings,
        release_name=release_name or settings.tag,
        if_exists=if_exists,
        template_path=template_path,
    )

    _print_summary(
        settings=settings,
        result=result,
        notify_settings=notify_settings,
        dry_run=dry_run,
        json_output=run_ctx.json_output,
    )


def _print_summary(
    *,
    settings: config.Settings,
    result: release_flow.ReleaseResult,
    notify_settings: config.NotifySettings | None,
    dry_run: bool,
    json_output: bool,
) -> None:
    """Business logic seam - no click objects below this line. Prints what was (or,
    under --dry-run, would be) created: tag, release, uploaded packages, and the
    changelog. Does not call notify.send_notification: the notification is always
    previewed here, never sent - actually sending it is a future slice.
    """
    logger.debug("Building summary for tag={}", settings.tag)
    subject = f"Release {settings.tag}"
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "gitlab_url": settings.gitlab_url,
        "project_id": settings.project_id,
        "tag": settings.tag,
        "ref": settings.ref,
        "tag_created": result.tag_created,
        "release_url": result.release_url,
        "packages": result.package_urls,
        "changelog": result.changelog_text,
        # settings.token is intentionally never placed into this dict.
    }
    if notify_settings is not None:
        summary["notify_preview"] = {"to": notify_settings.smtp_to, "subject": subject}

    if json_output:
        click.echo(json.dumps(summary))
        return

    verb = "Would create" if dry_run else "Created"
    prefix = "[dry-run] " if dry_run else ""
    click.echo(
        f"{prefix}{verb} tag {settings.tag!r} at ref {settings.ref!r} on project "
        f"{settings.project_id!r} ({settings.gitlab_url})."
    )
    for pkg in result.package_urls:
        label = "Would upload" if dry_run else "Uploaded"
        click.echo(f"{prefix}{label} {pkg['name']} -> {pkg['url']}")
    if result.release_url:
        click.echo(f"Release: {result.release_url}")
    click.echo("\n--- Changelog preview ---" if dry_run else "\n--- Changelog ---")
    click.echo(result.changelog_text)
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
