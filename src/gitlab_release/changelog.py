"""Commit/contributor/approver collection and Jinja2 rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from gitlab_release.errors import TemplateError
from gitlab_release.gitlab_client import RawCommit

if TYPE_CHECKING:
    from gitlab_release.config import Settings
    from gitlab_release.gitlab_client import GitlabClient

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


@dataclass(frozen=True)
class Contributor:
    name: str
    email: str


def dedupe_contributors(commits: list[ParsedCommit]) -> list[Contributor]:
    seen_emails: set[str] = set()
    seen_names: set[str] = set()
    result: list[Contributor] = []
    for c in commits:
        if c.author_email in seen_emails or c.author_name in seen_names:
            continue
        seen_emails.add(c.author_email)
        seen_names.add(c.author_name)
        result.append(Contributor(name=c.author_name, email=c.author_email))
    return result


@dataclass(frozen=True)
class ChangelogContext:
    tag: str
    previous_tag: str | None
    project: str
    released_at: str
    commits: list[ParsedCommit]
    contributors: list[Contributor]
    approvers: list[str]
    packages: list[dict[str, str]]


def build_context(
    client: GitlabClient,
    settings: Settings,
    packages: list[dict[str, str]] | None = None,
) -> ChangelogContext:
    previous_tag = client.previous_tag(before=settings.tag)
    # Compare against settings.ref (always a resolvable commit SHA), not settings.tag:
    # the tag may not exist on the server yet (this run may be about to create it), in
    # which case it isn't a resolvable ref. `ref` points at the same commit the tag does
    # or will, by construction (create_tag posts {"tag_name": tag, "ref": ref}).
    raw_commits = client.compare_commits(from_=previous_tag, to=settings.ref)
    commits = [parse_commit(rc) for rc in raw_commits]
    contributors = dedupe_contributors(commits)
    approvers_by_sha = client.mr_approvers([c.sha for c in commits])
    approvers = sorted({name for names in approvers_by_sha.values() for name in names})
    return ChangelogContext(
        tag=settings.tag,
        previous_tag=previous_tag,
        project=settings.project_id,
        released_at=datetime.now(UTC).isoformat(),
        commits=commits,
        contributors=contributors,
        approvers=approvers,
        packages=list(packages or []),
    )


_DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "templates"
_DEFAULT_TEMPLATE_NAME = "changelog.md.j2"


def render(context: ChangelogContext, template_path: Path | None = None) -> str:
    if template_path is not None:
        loader = FileSystemLoader(str(template_path.parent))
        template_name = template_path.name
    else:
        loader = FileSystemLoader(str(_DEFAULT_TEMPLATE_DIR))
        template_name = _DEFAULT_TEMPLATE_NAME

    env = SandboxedEnvironment(
        loader=loader,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    commits_by_type = group_commits_by_type(context.commits)
    try:
        template = env.get_template(template_name)
        return template.render(
            tag=context.tag,
            previous_tag=context.previous_tag,
            project=context.project,
            released_at=context.released_at,
            commits_by_type=commits_by_type,
            contributors=context.contributors,
            approvers=context.approvers,
            packages=context.packages,
        )
    except Exception as exc:
        raise TemplateError(f"Failed to render changelog template: {exc}") from exc
