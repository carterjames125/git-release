import pytest
import requests
import responses

from gitlab_release.errors import GitLabAPIError
from gitlab_release.gitlab_client import GitlabClient
from tests.gitlab_fixtures import (
    API,
    PROJECT_ID,
    register_commit_merge_requests,
    register_commits_list,
    register_compare,
    register_mr,
    register_mr_approvals,
    register_project,
    register_tags,
)


@responses.activate
def test_previous_tag_returns_tag_immediately_before_by_commit_date() -> None:
    register_project()
    register_tags(
        [
            {"name": "v1.0.0", "committed_date": "2026-01-01T00:00:00.000Z"},
            {"name": "v1.1.0", "committed_date": "2026-02-01T00:00:00.000Z"},
            {"name": "v1.2.0", "committed_date": "2026-03-01T00:00:00.000Z"},
        ]
    )

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.previous_tag(before="v1.2.0") == "v1.1.0"


@responses.activate
def test_previous_tag_none_for_first_tag_ever() -> None:
    register_project()
    register_tags([{"name": "v1.0.0", "committed_date": "2026-01-01T00:00:00.000Z"}])

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.previous_tag(before="v1.0.0") is None


@responses.activate
def test_previous_tag_sorts_by_commit_date_not_name() -> None:
    register_project()
    # v1.9.0 committed AFTER v2.0.0 - date order is the only correct order here.
    register_tags(
        [
            {"name": "v2.0.0", "committed_date": "2026-01-01T00:00:00.000Z"},
            {"name": "v1.9.0", "committed_date": "2026-02-01T00:00:00.000Z"},
        ]
    )

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.previous_tag(before="v1.9.0") == "v2.0.0"


@responses.activate
def test_previous_tag_sorts_chronologically_across_differing_utc_offsets() -> None:
    # '...T09:00:00+00:00' sorts BEFORE '...T10:00:00+02:00' as plain strings, but the
    # second timestamp is actually 08:00 UTC - earlier in real time. A lexicographic sort
    # gets this backwards; a datetime-aware sort gets it right.
    register_project()
    register_tags(
        [
            {"name": "v1.0.0", "committed_date": "2026-03-01T10:00:00.000+02:00"},
            {"name": "v1.1.0", "committed_date": "2026-03-01T09:00:00.000+00:00"},
        ]
    )

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.previous_tag(before="v1.1.0") == "v1.0.0"


@responses.activate
def test_compare_commits_returns_raw_commits() -> None:
    register_project()
    register_compare(
        commits=[
            {
                "id": "abc123def456",
                "title": "feat: add widget",
                "message": "feat: add widget\n\nBody text here.",
                "author_name": "Alice",
                "author_email": "alice@example.com",
            }
        ]
    )

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    commits = client.compare_commits(from_="v1.0.0", to="v1.1.0")

    assert len(commits) == 1
    assert commits[0].sha == "abc123def456"
    assert commits[0].title == "feat: add widget"
    assert commits[0].author_email == "alice@example.com"


@responses.activate
def test_compare_commits_from_none_covers_full_history() -> None:
    # from_=None means "no previous tag" - there's nothing to diff against, so this
    # must list every commit reachable from `to` via the commits endpoint, NOT send
    # from="" to the compare endpoint (which GitLab can't resolve as a ref).
    register_project()
    register_commits_list(
        [
            {
                "id": "abc123def456",
                "title": "feat: add widget",
                "message": "feat: add widget\n\nBody text here.",
                "author_name": "Alice",
                "author_email": "alice@example.com",
            }
        ]
    )
    # No compare-endpoint mock registered at all: if the implementation still hits
    # /repository/compare, `responses` raises ConnectionError and this test fails loudly
    # rather than silently matching a stale mock.

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    commits = client.compare_commits(from_=None, to="v1.0.0")

    assert len(commits) == 1
    assert commits[0].sha == "abc123def456"
    assert commits[0].title == "feat: add widget"
    assert commits[0].author_email == "alice@example.com"

    # Confirm the actual HTTP call: right endpoint, right ref_name query param.
    sent = responses.calls[-1].request
    assert sent.url == f"{API}/projects/{PROJECT_ID}/repository/commits?ref_name=v1.0.0"


@responses.activate
def test_compare_commits_from_none_empty_history() -> None:
    register_project()
    register_commits_list([])

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    commits = client.compare_commits(from_=None, to="v1.0.0")

    assert commits == []


@responses.activate
def test_init_wraps_connection_failure_as_gitlab_api_error() -> None:
    # DNS failure / connection refused / TLS error raise requests.exceptions.RequestException,
    # not gitlab.exceptions.GitlabError - these must still surface as GitLabAPIError (exit
    # code 3, "GitLab API problem"), not escape uncaught and get misclassified as an
    # internal bug (exit code 1) by cli.py's top-level handler.
    responses.add(
        responses.GET,
        f"{API}/projects/{PROJECT_ID}",
        body=requests.exceptions.ConnectionError("simulated connection refused"),
    )

    with pytest.raises(GitLabAPIError):
        GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")


@responses.activate
def test_mr_approvers_returns_names_by_sha() -> None:
    register_project()
    register_commit_merge_requests("abc123", [{"iid": 7}])
    register_mr(7)
    register_mr_approvals(7, approved_by=["Bob", "Carol"])

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    approvers = client.mr_approvers(["abc123"])

    assert approvers == {"abc123": ["Bob", "Carol"]}


@responses.activate
def test_mr_approvers_degrades_to_empty_on_403() -> None:
    register_project()
    register_commit_merge_requests("abc123", [{"iid": 7}])
    register_mr(7)
    register_mr_approvals(7, status=403)

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    approvers = client.mr_approvers(["abc123"])

    assert approvers == {}


@responses.activate
def test_mr_approvers_empty_when_commit_has_no_merge_requests() -> None:
    register_project()
    register_commit_merge_requests("abc123", [])

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    approvers = client.mr_approvers(["abc123"])

    assert approvers == {}


@responses.activate
def test_project_path_returns_slug_from_path_with_namespace() -> None:
    register_project()

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.project_path() == "project"
