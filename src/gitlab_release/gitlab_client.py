"""Thin wrapper over python-gitlab. The only module that talks to the GitLab API."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

import gitlab
import requests
from gitlab.exceptions import GitlabError, GitlabGetError

from gitlab_release.errors import ArtifactError, GitLabAPIError

T = TypeVar("T")

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BASE_DELAY_SECONDS = 0.5


@dataclass(frozen=True)
class RawCommit:
    sha: str
    title: str
    message: str
    author_name: str
    author_email: str


@dataclass(frozen=True)
class MrMetadata:
    approvers: list[str]
    labels: list[str]


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
        self._base_url = url.rstrip("/")
        ssl_verify: str | bool = str(ca_bundle) if ca_bundle else True
        if is_job_token:
            self._gl = gitlab.Gitlab(url, job_token=token, ssl_verify=ssl_verify)
        else:
            self._gl = gitlab.Gitlab(url, private_token=token, ssl_verify=ssl_verify)
        try:
            self._project = self._gl.projects.get(project_id)
        except (GitlabError, requests.exceptions.RequestException) as exc:
            raise GitLabAPIError(f"Failed to load project {project_id!r}: {exc}") from exc

    def _with_retries(self, func: Callable[[], T], *, description: str) -> T:
        """Bounded exponential backoff: up to 3 attempts, retrying only on 429 and
        5xx responses (and bare connection failures, which carry no status code at
        all). Any other error - including every other 4xx - is raised immediately."""
        attempt = 0
        while True:
            attempt += 1
            try:
                return func()
            except GitlabError as exc:
                if exc.response_code not in _RETRYABLE_STATUS or attempt >= _MAX_ATTEMPTS:
                    raise GitLabAPIError(f"Failed to {description}: {exc}") from exc
            except requests.exceptions.RequestException as exc:
                if attempt >= _MAX_ATTEMPTS:
                    raise GitLabAPIError(f"Failed to {description}: {exc}") from exc
            time.sleep(_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))

    def project_path(self) -> str:
        """The project's path slug (e.g. "project" from "group/project"), used as the
        default generic package name. No API call - reads the already-fetched Project."""
        return self._project.path_with_namespace.rsplit("/", 1)[-1]

    def tag_exists(self, tag: str) -> bool:
        def _get() -> bool:
            try:
                self._project.tags.get(tag)
            except GitlabGetError as exc:
                if exc.response_code == 404:
                    return False
                raise
            return True

        return self._with_retries(_get, description=f"check whether tag {tag!r} exists")

    def create_tag(self, *, tag: str, ref: str) -> None:
        def _create() -> None:
            self._project.tags.create({"tag_name": tag, "ref": ref})

        self._with_retries(_create, description=f"create tag {tag!r}")

    def release_exists(self, tag: str) -> bool:
        def _get() -> bool:
            try:
                self._project.releases.get(tag)
            except GitlabGetError as exc:
                if exc.response_code == 404:
                    return False
                raise
            return True

        return self._with_retries(_get, description=f"check whether release {tag!r} exists")

    def create_release(
        self, *, tag: str, name: str, description: str, links: list[dict[str, str]]
    ) -> str:
        def _create() -> str:
            release = self._project.releases.create(
                {
                    "tag_name": tag,
                    "name": name,
                    "description": description,
                    "assets": {
                        "links": [
                            {"name": link["name"], "url": link["url"], "link_type": "package"}
                            for link in links
                        ]
                    },
                }
            )
            web_url = release.web_url
            if not isinstance(web_url, str):
                raise GitLabAPIError(
                    f"Unexpected response type for release web_url: {type(web_url)!r}"
                )
            return web_url

        return self._with_retries(_create, description=f"create release {tag!r}")

    def package_download_url(self, *, name: str, version: str, file_name: str) -> str:
        return (
            f"{self._base_url}/api/v4/projects/{self._project.id}/packages/generic/"
            f"{name}/{version}/{file_name}"
        )

    def package_file_exists(self, *, name: str, version: str, file_name: str) -> bool:
        def _check() -> bool:
            packages = self._project.packages.list(
                package_type="generic",
                package_name=name,
                package_version=version,
                get_all=True,
            )
            for pkg in packages:
                file_names = {f.file_name for f in pkg.package_files.list(get_all=True)}
                if file_name in file_names:
                    return True
            return False

        return self._with_retries(
            _check, description=f"check whether package file {file_name!r} exists"
        )

    def upload_package_file(self, *, name: str, version: str, file_name: str, path: Path) -> str:
        def _upload() -> None:
            self._project.generic_packages.upload(
                package_name=name, package_version=version, file_name=file_name, path=path
            )

        try:
            self._with_retries(_upload, description=f"upload package file {file_name!r}")
        except GitLabAPIError as exc:
            # Upload failure is an artifact-domain problem (exit code 4), not a
            # GitLab-auth/permissions/5xx problem (exit code 3), even though it's
            # raised by the same retry helper as the other methods here.
            raise ArtifactError(exc.message) from exc
        return self.package_download_url(name=name, version=version, file_name=file_name)

    def previous_tag(self, *, before: str) -> str | None:
        try:
            tags = self._project.tags.list(all=True)
        except (GitlabError, requests.exceptions.RequestException) as exc:
            raise GitLabAPIError(f"Failed to list tags: {exc}") from exc
        # committed_date includes the committer's UTC offset (not normalized to Z), so a
        # plain string sort can disagree with real chronological order across offsets.
        # Parse before comparing.
        ordered = sorted(tags, key=lambda t: datetime.fromisoformat(t.commit["committed_date"]))
        names = [t.name for t in ordered]
        try:
            idx = names.index(before)
        except ValueError:
            # `before` isn't in the list yet - normal when it's about to be created by
            # this same run. Its "previous tag" is whatever is currently newest (the new
            # one becomes the newest once created). An empty list means there truly are
            # no tags yet - that's the only case where None (first-ever release) is right.
            return names[-1] if names else None
        return names[idx - 1] if idx > 0 else None

    def compare_commits(self, *, from_: str | None, to: str) -> list[RawCommit]:
        if from_ is None:
            # No previous tag: there's no range to compare, so list every commit
            # reachable from `to` instead of sending an empty (unresolvable) `from`.
            try:
                commit_list = self._project.commits.list(ref_name=to, get_all=True)
            except (GitlabError, requests.exceptions.RequestException) as exc:
                raise GitLabAPIError(f"Failed to list commits: {exc}") from exc
            return [
                RawCommit(
                    sha=c.id,
                    title=c.title,
                    message=c.message,
                    author_name=c.author_name,
                    author_email=c.author_email,
                )
                for c in commit_list
            ]
        try:
            result = self._project.repository_compare(from_=from_, to=to)
        except (GitlabError, requests.exceptions.RequestException) as exc:
            raise GitLabAPIError(f"Failed to compare commits: {exc}") from exc
        if not isinstance(result, dict):
            # repository_compare()'s stub return type is dict[str, Any] | requests.Response;
            # the Response branch only occurs for raw/streaming requests, which this call
            # never makes. Narrow explicitly so mypy --strict can verify the indexing below.
            raise GitLabAPIError(
                f"Unexpected response type from repository_compare: {type(result)!r}"
            )
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

    def mr_metadata(self, commit_shas: Sequence[str]) -> dict[str, MrMetadata]:
        metadata: dict[str, MrMetadata] = {}
        for sha in commit_shas:
            try:
                commit = self._project.commits.get(sha, lazy=True)
                mrs = commit.merge_requests()
            except (GitlabError, requests.exceptions.RequestException) as exc:
                raise GitLabAPIError(f"Failed to look up merge requests for {sha}: {exc}") from exc

            names: list[str] = []
            labels: list[str] = []
            for mr_data in mrs:
                try:
                    mr = self._project.mergerequests.get(mr_data["iid"])
                except GitlabGetError as exc:
                    if exc.response_code in (403, 404):
                        continue
                    raise GitLabAPIError(f"Failed to fetch MR {mr_data['iid']}: {exc}") from exc
                # Labels come off the MR object itself, already fetched above - capture them
                # before the separate approvals call, which can independently 403 (Free tier,
                # no approval rules configured) without losing labels already in hand.
                labels.extend(mr.labels)
                try:
                    approval = mr.approvals.get()
                    names.extend(a["user"]["name"] for a in approval.approved_by)
                except GitlabGetError as exc:
                    if exc.response_code in (403, 404):
                        continue
                    raise GitLabAPIError(
                        f"Failed to fetch approvals for MR {mr_data['iid']}: {exc}"
                    ) from exc
            if names or labels:
                metadata[sha] = MrMetadata(approvers=names, labels=list(dict.fromkeys(labels)))
        return metadata
