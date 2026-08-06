from gitlab_release.changelog import (
    Contributor,
    dedupe_contributors,
    group_commits_by_type,
    parse_commit,
)
from gitlab_release.gitlab_client import RawCommit


def test_parse_commit_extracts_conventional_type_and_scope() -> None:
    raw = RawCommit(
        sha="abc123",
        title="feat(cli): add --notify flag",
        message="feat(cli): add --notify flag\n\nLets users opt into email notifications.",
        author_name="Alice",
        author_email="alice@example.com",
    )

    parsed = parse_commit(raw)

    assert parsed.type == "feat"
    assert parsed.scope == "cli"
    assert parsed.subject == "add --notify flag"
    assert parsed.body == "Lets users opt into email notifications."
    assert parsed.sha == "abc123"


def test_parse_commit_unparseable_title_goes_to_other_bucket() -> None:
    raw = RawCommit(
        sha="def456",
        title="quick fix for the thing",
        message="quick fix for the thing",
        author_name="Bob",
        author_email="bob@example.com",
    )

    parsed = parse_commit(raw)

    assert parsed.type == "other"
    assert parsed.scope is None
    assert parsed.subject == "quick fix for the thing"


def test_group_commits_by_type_preserves_first_seen_order_other_last() -> None:
    commits = [
        parse_commit(RawCommit("a", "fix: bug one", "fix: bug one", "A", "a@example.com")),
        parse_commit(RawCommit("b", "no type here", "no type here", "B", "b@example.com")),
        parse_commit(RawCommit("c", "feat: thing", "feat: thing", "C", "c@example.com")),
        parse_commit(RawCommit("d", "fix: bug two", "fix: bug two", "D", "d@example.com")),
    ]

    grouped = group_commits_by_type(commits)

    assert list(grouped.keys()) == ["fix", "feat", "other"]
    assert [c.sha for c in grouped["fix"]] == ["a", "d"]
    assert [c.sha for c in grouped["other"]] == ["b"]


def test_dedupe_contributors_by_email_first() -> None:
    commits = [
        parse_commit(RawCommit("a", "fix: x", "fix: x", "Alice", "alice@example.com")),
        parse_commit(RawCommit("b", "fix: y", "fix: y", "Alice", "alice@example.com")),
    ]

    contributors = dedupe_contributors(commits)

    assert contributors == [Contributor(name="Alice", email="alice@example.com")]


def test_dedupe_contributors_by_name_when_email_differs() -> None:
    commits = [
        parse_commit(RawCommit("a", "fix: x", "fix: x", "Alice", "alice@work.com")),
        parse_commit(RawCommit("b", "fix: y", "fix: y", "Alice", "alice@personal.com")),
    ]

    contributors = dedupe_contributors(commits)

    assert len(contributors) == 1
    assert contributors[0].name == "Alice"


def test_dedupe_contributors_keeps_distinct_people() -> None:
    commits = [
        parse_commit(RawCommit("a", "fix: x", "fix: x", "Alice", "alice@example.com")),
        parse_commit(RawCommit("b", "fix: y", "fix: y", "Bob", "bob@example.com")),
    ]

    contributors = dedupe_contributors(commits)

    assert {c.name for c in contributors} == {"Alice", "Bob"}
