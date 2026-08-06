from pathlib import Path

import pytest

from gitlab_release.changelog import (
    ChangelogContext,
    Contributor,
    build_context,
    dedupe_contributors,
    group_commits_by_type,
    parse_commit,
    render,
)
from gitlab_release.config import Settings
from gitlab_release.errors import TemplateError
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


class _FakeGitlabClient:
    def __init__(
        self,
        previous: str | None,
        commits: list[RawCommit],
        approvers: dict[str, list[str]],
    ) -> None:
        self._previous = previous
        self._commits = commits
        self._approvers = approvers

    def previous_tag(self, *, before: str) -> str | None:
        return self._previous

    def compare_commits(self, *, from_: str | None, to: str) -> list[RawCommit]:
        return self._commits

    def mr_approvers(self, commit_shas: list[str]) -> dict[str, list[str]]:
        return self._approvers


def test_build_context_assembles_full_changelog_context() -> None:
    commits = [
        RawCommit("a", "feat: thing", "feat: thing", "Alice", "alice@example.com"),
        RawCommit("b", "fix: bug", "fix: bug", "Bob", "bob@example.com"),
    ]
    client = _FakeGitlabClient(previous="v0.9.0", commits=commits, approvers={"a": ["Carol"]})
    settings = Settings(
        gitlab_url="https://gitlab.example.com",
        project_id="42",
        token="t",
        is_job_token=False,
        tag="v1.0.0",
        ref="abc",
        ca_bundle=None,
    )

    context: ChangelogContext = build_context(client, settings)

    assert context.tag == "v1.0.0"
    assert context.previous_tag == "v0.9.0"
    assert context.project == "42"
    assert len(context.commits) == 2
    assert {c.name for c in context.contributors} == {"Alice", "Bob"}
    assert context.approvers == ["Carol"]
    assert context.packages == []


def _sample_context() -> ChangelogContext:
    return ChangelogContext(
        tag="v1.0.0",
        previous_tag="v0.9.0",
        project="42",
        released_at="2026-08-05T00:00:00+00:00",
        commits=[
            parse_commit(
                RawCommit(
                    "abcdef1234",
                    "feat: add widget",
                    "feat: add widget",
                    "Alice",
                    "alice@example.com",
                )
            ),
            parse_commit(
                RawCommit(
                    "2345678901",
                    "unparseable commit",
                    "unparseable commit",
                    "Bob",
                    "bob@example.com",
                )
            ),
        ],
        contributors=[
            Contributor(name="Alice", email="alice@example.com"),
            Contributor(name="Bob", email="bob@example.com"),
        ],
        approvers=["Carol"],
        packages=[],
    )


def test_render_default_template_includes_all_sections_in_order() -> None:
    text = render(_sample_context())

    assert "# v1.0.0" in text
    assert "Changes since v0.9.0." in text
    assert "## feat" in text
    assert "add widget (abcdef12)" in text
    assert "## other" in text
    assert "unparseable commit (23456789)" in text
    assert "## Contributors" in text
    assert "- Alice" in text
    assert "- Bob" in text
    assert "## Approvers" in text
    assert "- Carol" in text
    assert "## Packages" not in text

    for a, b in [
        ("## feat", "## other"),
        ("## other", "## Contributors"),
        ("## Contributors", "## Approvers"),
    ]:
        assert text.index(a) < text.index(b)


def test_render_first_release_has_no_previous_tag_line() -> None:
    context = ChangelogContext(
        tag="v1.0.0",
        previous_tag=None,
        project="42",
        released_at="2026-08-05T00:00:00+00:00",
        commits=[],
        contributors=[],
        approvers=[],
        packages=[],
    )

    text = render(context)

    assert "Changes since" not in text


def test_render_custom_template_override(tmp_path: Path) -> None:
    custom = tmp_path / "custom.md.j2"
    custom.write_text("Custom changelog for {{ tag }}\n")

    text = render(_sample_context(), template_path=custom)

    assert text == "Custom changelog for v1.0.0\n"


def test_render_strict_undefined_raises_template_error_on_typo(tmp_path: Path) -> None:
    custom = tmp_path / "typo.md.j2"
    custom.write_text("{{ nonexistent_variable }}\n")

    with pytest.raises(TemplateError):
        render(_sample_context(), template_path=custom)
