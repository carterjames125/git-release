"""Glob source path, validate, and normalize package version. Read-only, no GitLab
imports - never writes to the (typically read-only-mounted) source path."""

from __future__ import annotations

import re
from pathlib import Path

from gitlab_release.errors import ArtifactError

_ILLEGAL_VERSION_CHARS = re.compile(r"[^A-Za-z0-9.\-+]")


def discover(source_path: Path, pattern: str) -> list[Path]:
    matches = sorted(source_path.glob(pattern))
    if not matches:
        raise ArtifactError(f"No files matched pattern {pattern!r} under {source_path}")
    for match in matches:
        if not match.is_file():
            raise ArtifactError(f"{match} matched {pattern!r} but is not a regular file")
    return matches


def normalize_version(tag: str) -> str:
    stripped = tag[1:] if tag[:1] in ("v", "V") else tag
    normalized = _ILLEGAL_VERSION_CHARS.sub("", stripped)
    if not normalized:
        raise ArtifactError(
            f"Tag {tag!r} normalizes to an empty package version after removing "
            "characters the Generic Package Registry rejects"
        )
    return normalized
