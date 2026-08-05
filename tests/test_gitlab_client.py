import responses

from gitlab_release.gitlab_client import GitlabClient
from tests.gitlab_fixtures import PROJECT_ID, register_compare, register_project, register_tags


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
    register_project()
    register_compare(commits=[])

    client = GitlabClient(url="https://gitlab.example.com", project_id=PROJECT_ID, token="t")
    commits = client.compare_commits(from_=None, to="v1.0.0")

    assert commits == []
