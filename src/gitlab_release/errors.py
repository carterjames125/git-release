"""Exception hierarchy mapped to process exit codes (see CLAUDE.md's exit-code table).

Business-logic modules raise a ReleaseError subclass (or let one propagate); cli.py's
single top-level handler reads `exc.exit_code` and `exc.message` and never needs to
know which module raised it. The full hierarchy is defined now, even though only
InternalError (1) and ConfigError (2) are reachable in this slice, because exit codes
are part of the CLI's public contract for CI callers - later slices must slot into
these names, not invent new ones.
"""

from __future__ import annotations


class ReleaseError(Exception):
    """Base for all handled errors. `exit_code` is a class attribute, not per-instance,
    so every raise site for a given failure category is automatically consistent."""

    exit_code: int = 1

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InternalError(ReleaseError):
    """Unexpected/internal error (bug, unhandled exception). Exit code 1."""

    exit_code = 1


class ConfigError(ReleaseError):
    """Usage or configuration error (missing/invalid settings, bad flag combo). Exit code 2."""

    exit_code = 2


class GitLabAPIError(ReleaseError):
    """GitLab API error: auth, permissions, 5xx after retries exhausted. Exit code 3.
    Unused in this slice; gitlab_client.py will raise this once it exists."""

    exit_code = 3


class ArtifactError(ReleaseError):
    """Artifact error: empty/unreadable source path, upload failure. Exit code 4.
    Unused in this slice; artifacts.py will raise this once it exists."""

    exit_code = 4


class TemplateError(ReleaseError):
    """Jinja2 template rendering error. Exit code 5.
    Unused in this slice; changelog.py will raise this once it exists."""

    exit_code = 5
