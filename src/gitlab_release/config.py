"""Settings dataclass, CI-default env-var resolution, up-front aggregated validation.

Pure: no logger calls, no click imports. Must be fully testable and usable without
Click (cli.py resolves each CLI-flag/primary-envvar pair via Click's own `envvar=`
machinery and passes the already-resolved-or-None value in here; this module's only
job is applying the CI-predefined-variable fallback and producing one aggregated error
if anything is still missing).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import ClassVar

from gitlab_release.errors import ConfigError


@dataclass(frozen=True, repr=False)
class Settings:
    gitlab_url: str
    project_id: str
    token: str
    is_job_token: bool
    tag: str
    ref: str
    ca_bundle: Path | None = None

    _SECRET_FIELDS: ClassVar[frozenset[str]] = frozenset({"token"})

    def __repr__(self) -> str:
        parts = [f"{f.name}={self._masked_value(f.name)!r}" for f in fields(self)]
        return f"Settings({', '.join(parts)})"

    def _masked_value(self, name: str) -> object:
        return "***MASKED***" if name in self._SECRET_FIELDS else getattr(self, name)

    def secret_values(self) -> tuple[str, ...]:
        """Values to redact from logs. Same source list (`_SECRET_FIELDS`) as
        `__repr__` so adding a secret field only requires updating one place."""
        return tuple(getattr(self, name) for name in self._SECRET_FIELDS if getattr(self, name))


def load_settings(
    *,
    gitlab_url: str | None,
    project_id: str | None,
    token: str | None,
    tag: str | None,
    ca_bundle: Path | None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """`gitlab_url`/`project_id`/`token`/`tag` are whatever Click already resolved from
    an explicit flag or its primary envvar (GITLAB_URL / GITLAB_PROJECT_ID /
    GITLAB_TOKEN / RELEASE_TAG) - pass None if unset. This function only applies the
    CI-predefined-variable fallback and validates. `ref` has no CLI flag or override
    variable at all - it is read straight from CI_COMMIT_SHA.
    """
    resolved_env = env if env is not None else os.environ

    resolved_url = gitlab_url or resolved_env.get("CI_SERVER_URL")
    resolved_project = project_id or resolved_env.get("CI_PROJECT_ID")
    resolved_token, is_job_token = _resolve_token(token, resolved_env)
    resolved_tag = tag or resolved_env.get("CI_COMMIT_TAG")
    resolved_ref = resolved_env.get("CI_COMMIT_SHA")

    missing = []
    if not resolved_url:
        missing.append("gitlab_url (set --gitlab-url, GITLAB_URL, or CI_SERVER_URL)")
    if not resolved_project:
        missing.append("project_id (set --project-id, GITLAB_PROJECT_ID, or CI_PROJECT_ID)")
    if not resolved_token:
        missing.append("token (set --token, GITLAB_TOKEN, or CI_JOB_TOKEN)")
    if not resolved_tag:
        missing.append("tag (set --tag, RELEASE_TAG, or CI_COMMIT_TAG)")
    if not resolved_ref:
        missing.append("ref (set CI_COMMIT_SHA)")
    if missing:
        raise ConfigError(
            "Missing required configuration:\n" + "\n".join(f"  - {m}" for m in missing)
        )

    assert resolved_url and resolved_project and resolved_token and resolved_tag and resolved_ref
    return Settings(
        gitlab_url=resolved_url,
        project_id=resolved_project,
        token=resolved_token,
        is_job_token=is_job_token,
        tag=resolved_tag,
        ref=resolved_ref,
        ca_bundle=ca_bundle,
    )


def _resolve_token(token: str | None, env: Mapping[str, str]) -> tuple[str | None, bool]:
    if token:
        return token, False
    job_token = env.get("CI_JOB_TOKEN")
    if job_token:
        return job_token, True
    return None, False
