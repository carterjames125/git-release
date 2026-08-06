"""Thin wrapper over python-gitlab. The only module that talks to the GitLab API."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import gitlab
from gitlab.exceptions import GitlabError, GitlabGetError

from gitlab_release.errors import GitLabAPIError


@dataclass(frozen=True)
class RawCommit:
    sha: str
    title: str
    message: str
    author_name: str
    author_email: str


class GitlabClient:
    """Constructed once from `Settings`, passed down to changelog.py."""

    def __init__(
        self,
        *,
        url: str,
        project_id: str,
        token: str,
        is_job_token: bool = False,
        ca_bundle: Path | None = None,
    ) -> None:
        ssl_verify: str | bool = str(ca_bundle) if ca_bundle else True
        if is_job_token:
            self._gl = gitlab.Gitlab(url, job_token=token, ssl_verify=ssl_verify)
        else:
            self._gl = gitlab.Gitlab(url, private_token=token, ssl_verify=ssl_verify)
        try:
            self._project = self._gl.projects.get(project_id)
        except GitlabError as exc:
            raise GitLabAPIError(f"Failed to load project {project_id!r}: {exc}") from exc

    def previous_tag(self, *, before: str) -> str | None:
        try:
            tags = self._project.tags.list(all=True)
        except GitlabError as exc:
            raise GitLabAPIError(f"Failed to list tags: {exc}") from exc
        ordered = sorted(tags, key=lambda t: t.commit["committed_date"])
        names = [t.name for t in ordered]
        try:
            idx = names.index(before)
        except ValueError:
            return None
        return names[idx - 1] if idx > 0 else None

    def compare_commits(self, *, from_: str | None, to: str) -> list[RawCommit]:
        try:
            result = self._project.repository_compare(from_=from_ or "", to=to)
        except GitlabError as exc:
            raise GitLabAPIError(f"Failed to compare commits: {exc}") from exc
        return [
            RawCommit(
                sha=c["id"],
                title=c["title"],
                message=c["message"],
                author_name=c["author_name"],
                author_email=c["author_email"],
            )
            for c in result["commits"]
        ]

    def mr_approvers(self, commit_shas: Sequence[str]) -> dict[str, list[str]]:
        approvers: dict[str, list[str]] = {}
        for sha in commit_shas:
            try:
                commit = self._project.commits.get(sha, lazy=True)
                mrs = commit.merge_requests()
            except GitlabError as exc:
                raise GitLabAPIError(f"Failed to look up merge requests for {sha}: {exc}") from exc

            names: list[str] = []
            for mr_data in mrs:
                try:
                    mr = self._project.mergerequests.get(mr_data["iid"])
                    approval = mr.approvals.get()
                    names.extend(a["user"]["name"] for a in approval.approved_by)
                except GitlabGetError as exc:
                    if exc.response_code in (403, 404):
                        continue
                    raise GitLabAPIError(
                        f"Failed to fetch approvals for MR {mr_data['iid']}: {exc}"
                    ) from exc
            if names:
                approvers[sha] = names
        return approvers
