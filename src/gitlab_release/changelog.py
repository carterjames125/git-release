"""Commit/contributor/approver collection and Jinja2 rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass

from gitlab_release.gitlab_client import RawCommit

_CONVENTIONAL_RE = re.compile(r"^(?P<type>[a-z]+)(\((?P<scope>[^)]+)\))?: (?P<subject>.+)$")


@dataclass(frozen=True)
class ParsedCommit:
    sha: str
    type: str
    scope: str | None
    subject: str
    body: str
    author_name: str
    author_email: str


def parse_commit(raw: RawCommit) -> ParsedCommit:
    match = _CONVENTIONAL_RE.match(raw.title)
    if match:
        commit_type = match.group("type")
        scope = match.group("scope")
        subject = match.group("subject")
    else:
        commit_type = "other"
        scope = None
        subject = raw.title
    body = raw.message[len(raw.title) :].strip()
    return ParsedCommit(
        sha=raw.sha,
        type=commit_type,
        scope=scope,
        subject=subject,
        body=body,
        author_name=raw.author_name,
        author_email=raw.author_email,
    )


def group_commits_by_type(commits: list[ParsedCommit]) -> dict[str, list[ParsedCommit]]:
    grouped: dict[str, list[ParsedCommit]] = {}
    for commit in commits:
        grouped.setdefault(commit.type, []).append(commit)
    if "other" in grouped:
        grouped["other"] = grouped.pop("other")
    return grouped
