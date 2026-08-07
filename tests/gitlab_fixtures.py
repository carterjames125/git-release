"""Shared GitLab REST API mocking helpers. Not collected by pytest (no test_ prefix)."""

from __future__ import annotations

import responses

BASE_URL = "https://gitlab.example.com"
API = f"{BASE_URL}/api/v4"
PROJECT_ID = "42"


def register_project() -> None:
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}",
        json={"id": 42, "path_with_namespace": "group/project"},
        status=200,
    )


def register_tags(tags: list[dict[str, str]]) -> None:
    """`tags` is a list of {"name": ..., "committed_date": ...} dicts."""
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/repository/tags",
        json=[{"name": t["name"], "commit": {"committed_date": t["committed_date"]}} for t in tags],
        status=200,
    )


def register_compare(commits: list[dict[str, str]]) -> None:
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/repository/compare",
        json={"commits": commits, "diffs": []},
        status=200,
    )


def register_commits_list(commits: list[dict[str, str]]) -> None:
    """Mocks GET .../repository/commits, hit by `commits.list(ref_name=..., get_all=True)`
    for the "no previous tag" case (compare_commits' from_=None branch)."""
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/repository/commits",
        json=commits,
        status=200,
    )


def register_commit_merge_requests(sha: str, mrs: list[dict[str, int]]) -> None:
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/repository/commits/{sha}/merge_requests",
        json=mrs,
        status=200,
    )


def register_mr(iid: int, *, labels: list[str] | None = None) -> None:
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/merge_requests/{iid}",
        json={"id": iid, "iid": iid, "labels": labels or []},
        status=200,
    )


def register_mr_approvals(
    iid: int, approved_by: list[str] | None = None, status: int = 200
) -> None:
    body = (
        {"approved_by": [{"user": {"name": n}} for n in (approved_by or [])]}
        if status == 200
        else {"message": "error"}
    )
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/merge_requests/{iid}/approvals",
        json=body,
        status=status,
    )


def register_tag_get(tag: str, *, exists: bool) -> None:
    status = 200 if exists else 404
    body = {"name": tag} if exists else {"message": "404 Tag Not Found"}
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/repository/tags/{tag}",
        json=body,
        status=status,
    )


def register_tag_create(tag: str, *, status: int = 201) -> None:
    responses.add(
        responses.POST,
        f"{API}/projects/{PROJECT_ID}/repository/tags",
        json={"name": tag},
        status=status,
    )


def register_release_get(tag: str, *, exists: bool) -> None:
    status = 200 if exists else 404
    body = {"tag_name": tag} if exists else {"message": "404 Release Not Found"}
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/releases/{tag}",
        json=body,
        status=status,
    )


def register_release_create(
    tag: str,
    *,
    web_url: str = "https://gitlab.example.com/group/project/-/releases/v1.2.3",
) -> None:
    responses.add(
        responses.POST,
        f"{API}/projects/{PROJECT_ID}/releases",
        json={"tag_name": tag, "web_url": web_url},
        status=201,
    )


def register_empty_release(tag: str = "v1.2.3") -> None:
    """The common case: one tag (no predecessor), zero commits in range - used by
    cli.py tests that only care about the dry-run flow working, not changelog content.
    Single tag => previous_tag is None => compare_commits hits the commits-list endpoint,
    not the compare endpoint."""
    register_project()
    register_tags([{"name": tag, "committed_date": "2026-01-01T00:00:00.000Z"}])
    register_commits_list([])


def register_packages_list(packages: list[dict[str, int]]) -> None:
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/packages",
        json=packages,
        status=200,
    )


def register_package_files_list(package_id: int, files: list[dict[str, str]]) -> None:
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/packages/{package_id}/package_files",
        json=files,
        status=200,
    )


def register_generic_package_upload(
    package_name: str, package_version: str, file_name: str, *, status: int = 201
) -> None:
    responses.add(
        responses.PUT,
        f"{API}/projects/{PROJECT_ID}/packages/generic/{package_name}/"
        f"{package_version}/{file_name}",
        json={"message": "201 Created"},
        status=status,
    )
