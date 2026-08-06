import json as json_module

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
    register_generic_package_upload,
    register_mr,
    register_mr_approvals,
    register_package_files_list,
    register_packages_list,
    register_project,
    register_release_create,
    register_release_get,
    register_tag_create,
    register_tag_get,
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
def test_previous_tag_falls_back_to_latest_when_before_not_yet_created() -> None:
    # `before` ("v2.0.0") isn't in the list - normal for a fresh tag this run is about
    # to create. The previous tag is whatever is currently newest.
    register_project()
    register_tags(
        [
            {"name": "v1.0.0", "committed_date": "2026-01-01T00:00:00.000Z"},
            {"name": "v1.1.0", "committed_date": "2026-02-01T00:00:00.000Z"},
        ]
    )

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.previous_tag(before="v2.0.0") == "v1.1.0"


@responses.activate
def test_previous_tag_none_when_before_not_found_and_no_tags_exist() -> None:
    register_project()
    register_tags([])

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.previous_tag(before="v1.0.0") is None


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


@responses.activate
def test_tag_exists_true_when_found() -> None:
    register_project()
    register_tag_get("v1.2.3", exists=True)

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.tag_exists("v1.2.3") is True


@responses.activate
def test_tag_exists_false_when_not_found() -> None:
    register_project()
    register_tag_get("v1.2.3", exists=False)

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.tag_exists("v1.2.3") is False


@responses.activate
def test_create_tag_posts_tag_name_and_ref() -> None:
    register_project()
    register_tag_create("v1.2.3")

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    client.create_tag(tag="v1.2.3", ref="abc123")

    sent = responses.calls[-1].request
    assert sent.method == "POST"
    body = json_module.loads(sent.body)
    assert body == {"tag_name": "v1.2.3", "ref": "abc123"}


@responses.activate
def test_create_tag_retries_on_500_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    import gitlab_release.gitlab_client as gc

    monkeypatch.setattr(gc.time, "sleep", lambda _seconds: None)
    register_project()
    responses.add(
        responses.POST,
        f"{API}/projects/{PROJECT_ID}/repository/tags",
        json={"message": "error"},
        status=500,
    )
    responses.add(
        responses.POST,
        f"{API}/projects/{PROJECT_ID}/repository/tags",
        json={"name": "v1.2.3"},
        status=201,
    )

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    client.create_tag(tag="v1.2.3", ref="abc123")  # does not raise

    assert len(responses.calls) == 3  # project GET + 2 tag POSTs


@responses.activate
def test_create_tag_does_not_retry_on_400(monkeypatch: pytest.MonkeyPatch) -> None:
    import gitlab_release.gitlab_client as gc

    monkeypatch.setattr(gc.time, "sleep", lambda _seconds: None)
    register_project()
    responses.add(
        responses.POST,
        f"{API}/projects/{PROJECT_ID}/repository/tags",
        json={"message": "Tag already exists"},
        status=400,
    )

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    with pytest.raises(GitLabAPIError):
        client.create_tag(tag="v1.2.3", ref="abc123")

    assert len(responses.calls) == 2  # project GET + exactly one tag POST, no retry


@responses.activate
def test_release_exists_true_when_found() -> None:
    register_project()
    register_release_get("v1.2.3", exists=True)

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.release_exists("v1.2.3") is True


@responses.activate
def test_release_exists_false_when_not_found() -> None:
    register_project()
    register_release_get("v1.2.3", exists=False)

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.release_exists("v1.2.3") is False


@responses.activate
def test_create_release_sends_assets_links_and_returns_web_url() -> None:
    register_project()
    register_release_create(
        "v1.2.3", web_url="https://gitlab.example.com/group/project/-/releases/v1.2.3"
    )

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    url = client.create_release(
        tag="v1.2.3",
        name="v1.2.3",
        description="## Changelog",
        links=[{"name": "app.rpm", "url": "https://example.com/app.rpm"}],
    )

    assert url == "https://gitlab.example.com/group/project/-/releases/v1.2.3"
    sent = responses.calls[-1].request
    body = json_module.loads(sent.body)
    assert body["tag_name"] == "v1.2.3"
    assert body["assets"]["links"] == [
        {"name": "app.rpm", "url": "https://example.com/app.rpm", "link_type": "package"}
    ]


@responses.activate
def test_package_download_url_format() -> None:
    register_project()

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    url = client.package_download_url(name="myapp", version="1.2.3", file_name="myapp.rpm")

    assert url == (
        "https://gitlab.example.com/api/v4/projects/42/packages/generic/myapp/1.2.3/myapp.rpm"
    )


@responses.activate
def test_package_file_exists_true_when_file_name_matches() -> None:
    register_project()
    register_packages_list([{"id": 99, "name": "myapp", "version": "1.2.3"}])
    register_package_files_list(99, [{"file_name": "myapp.rpm"}])

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.package_file_exists(name="myapp", version="1.2.3", file_name="myapp.rpm") is True


@responses.activate
def test_package_file_exists_false_when_no_matching_package() -> None:
    register_project()
    register_packages_list([])

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.package_file_exists(name="myapp", version="1.2.3", file_name="myapp.rpm") is False


@responses.activate
def test_package_file_exists_false_when_file_name_does_not_match() -> None:
    register_project()
    register_packages_list([{"id": 99, "name": "myapp", "version": "1.2.3"}])
    register_package_files_list(99, [{"file_name": "other.rpm"}])

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")

    assert client.package_file_exists(name="myapp", version="1.2.3", file_name="myapp.rpm") is False


@responses.activate
def test_upload_package_file_uploads_and_returns_download_url(tmp_path: object) -> None:
    import pathlib

    register_project()
    register_generic_package_upload("myapp", "1.2.3", "myapp.rpm")
    artifact = pathlib.Path(tmp_path) / "myapp.rpm"
    artifact.write_bytes(b"fake-rpm-contents")

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    url = client.upload_package_file(
        name="myapp", version="1.2.3", file_name="myapp.rpm", path=artifact
    )

    assert url == (
        "https://gitlab.example.com/api/v4/projects/42/packages/generic/myapp/1.2.3/myapp.rpm"
    )
