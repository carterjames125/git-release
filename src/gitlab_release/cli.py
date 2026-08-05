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

from gitlab_release import config
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
@handle_errors
def release(
    gitlab_url: str | None,
    project_id: str | None,
    token: str | None,
    tag: str | None,
    ca_bundle: Path | None,
    dry_run: bool,
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
    # Re-configure the sink now that secrets are known, before any further code runs.
    log_setup.configure_logging(
        level=run_ctx.level,
        json_output=run_ctx.json_output,
        verbose=run_ctx.verbose,
        secrets=settings.secret_values(),
    )

    if not dry_run:
        raise ConfigError(
            "--no-dry-run is not supported yet: release creation, artifact upload, "
            "changelog generation, and notification are not implemented in this build. "
            "Only --dry-run is available."
        )

    _run_release(settings=settings, json_output=run_ctx.json_output)


def _run_release(*, settings: config.Settings, json_output: bool) -> None:
    """Business logic seam - no click objects below this line, callable directly from
    tests or from a future orchestration layer. Currently prints a projection of the
    validated config, not a real GitLab-side preview (no gitlab_client.py exists yet).

    SEAM: once gitlab_client.py exists, construct a client here, query current
    tag/release state, and build a richer preview from it.
    """
    logger.debug("Building dry-run summary for tag={}", settings.tag)
    summary = {
        "dry_run": True,
        "gitlab_url": settings.gitlab_url,
        "project_id": settings.project_id,
        "tag": settings.tag,
        "ref": settings.ref,
        # settings.token is intentionally never placed into this dict.
    }
    if json_output:
        click.echo(json.dumps(summary))
    else:
        click.echo(
            f"[dry-run] Would create release for tag {settings.tag!r} at ref "
            f"{settings.ref!r} on project {settings.project_id!r} "
            f"({settings.gitlab_url}). No changes made."
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
