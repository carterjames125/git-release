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


def register_commit_merge_requests(sha: str, mrs: list[dict[str, int]]) -> None:
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/repository/commits/{sha}/merge_requests",
        json=mrs,
        status=200,
    )


def register_mr(iid: int) -> None:
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}/merge_requests/{iid}",
        json={"id": iid, "iid": iid},
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


def register_empty_release(tag: str = "v1.2.3") -> None:
    """The common case: one tag (no predecessor), zero commits in range - used by
    cli.py tests that only care about the dry-run flow working, not changelog content."""
    register_project()
    register_tags([{"name": tag, "committed_date": "2026-01-01T00:00:00.000Z"}])
    register_compare(commits=[])
