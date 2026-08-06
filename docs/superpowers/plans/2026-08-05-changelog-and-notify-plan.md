# Changelog Generation and Email Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a real changelog from GitLab commit/contributor/approver data and preview (never send, in this slice) an SMTP notification email, extending the `release --dry-run` command built in the first slice.

**Architecture:** A new `gitlab_client.py` (read-only: tag listing, commit comparison, MR approvers) feeds a new `changelog.py` (Conventional-Commit parsing, contributor dedup, Jinja2 rendering via a bundled `templates/changelog.md.j2`). A new `notify.py` implements real SMTP sending but is not yet called from `cli.py` — `--notify` only prints a preview line under dry-run, which remains the only supported mode. `config.py` gains `NotifySettings` for SMTP config, validated the same way `Settings` already is.

**Tech Stack:** `python-gitlab` (GitLab API), `jinja2` (`SandboxedEnvironment`, `StrictUndefined`), stdlib `smtplib`/`email.message`, `responses` for HTTP mocking in tests, `loguru`.

**Spec:** `docs/superpowers/specs/2026-08-05-changelog-and-notify-design.md`

## Global Constraints

- `mypy --strict` must pass on everything under `src/` (`[tool.mypy] files = ["src"]` in pyproject.toml — tests are not type-checked).
- `ruff check` and `ruff format --check` must pass.
- No live GitLab/SMTP calls in tests — mock HTTP with `responses`, mock `smtplib.SMTP` with `unittest.mock.patch`.
- `dataclass(frozen=True)` for all config/value objects; `pathlib.Path`, never string paths.
- Secrets (`token`, `smtp_password`) must never appear in `repr()`, log output, or CLI stdout — every new secret field goes into that dataclass's `_SECRET_FIELDS`.
- Exit codes per `errors.py`: `ConfigError` → 2, `GitLabAPIError` → 3, `TemplateError` → 5. No dependency introduces a new code.
- `--dry-run` stays the only supported mode this slice; `--no-dry-run` keeps raising `ConfigError` before any GitLab/SMTP call is attempted.
- Conventional Commits in commit messages for this work itself (`feat:`, `test:`, etc.), imperative subject, no `Co-Authored-By` trailers.
- `commits` are never dropped for failing to parse — unparseable titles go in an `other` bucket.
- `contributors` dedup by email first, then by display name.
- Approver lookups degrade to `[]` on 403/404 — never raise for that case.
- SMTP: no login attempted when `smtp_user`/`smtp_password` are absent (unauthenticated relay is normal); notification failures are logged and swallowed, never raised.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | add `python-gitlab`, `jinja2`; mypy override for gitlab's typing gaps |
| `src/gitlab_release/config.py` | add `NotifySettings` + `load_notify_settings` (modify) |
| `src/gitlab_release/gitlab_client.py` | new — thin read-only wrapper over `python-gitlab` |
| `src/gitlab_release/changelog.py` | new — commit parsing, contributor dedup, context assembly, Jinja2 rendering |
| `src/gitlab_release/templates/changelog.md.j2` | new — bundled default template |
| `src/gitlab_release/notify.py` | new — real SMTP sender, not yet wired into `cli.py` |
| `src/gitlab_release/cli.py` | modify — new options, `GitlabClient`/changelog wiring, notify preview |
| `tests/gitlab_fixtures.py` | new — shared `responses` mock helpers (not collected as tests) |
| `tests/test_config.py` | modify — `NotifySettings` tests |
| `tests/test_gitlab_client.py` | new |
| `tests/test_changelog.py` | new |
| `tests/test_notify.py` | new |
| `tests/test_cli.py` | modify — GitLab API mocks added to existing dry-run tests, new notify tests |

---

### Task 1: Add `python-gitlab` and `jinja2` dependencies

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `import gitlab` and `import jinja2` become available to every later task.

- [ ] **Step 1: Add the dependencies and a mypy override**

Edit `pyproject.toml`'s `dependencies` list to add `"python-gitlab>=4.0"` and `"jinja2>=3.1"` alongside the existing `rich-click`, `python-dotenv`, `loguru`. Also add, anywhere after the existing `[tool.mypy]` block:

```toml
[[tool.mypy.overrides]]
module = "gitlab.*"
ignore_missing_imports = true
```

(`python-gitlab` doesn't ship complete type stubs for every submodule; without this override, `mypy --strict` fails on `import gitlab` in Task 3 with "missing library stubs.")

- [ ] **Step 2: Sync and lock**

Run: `uv sync --all-extras && uv lock`
Expected: resolves cleanly, `python-gitlab` and `jinja2` (plus their transitive deps) appear in `uv.lock`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add python-gitlab and jinja2 dependencies"
```

---

### Task 2: `config.py` — `NotifySettings` and `load_notify_settings`

**Files:**
- Modify: `src/gitlab_release/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `gitlab_release.errors.ConfigError` (existing).
- Produces: `NotifySettings` (frozen dataclass: `smtp_host: str`, `smtp_port: int`, `smtp_user: str | None`, `smtp_password: str | None`, `smtp_from: str`, `smtp_to: str`, `smtp_starttls: bool`, plus `.secret_values() -> tuple[str, ...]` and masked `repr()`); `load_notify_settings(*, smtp_host, smtp_port, smtp_user, smtp_password, smtp_from, smtp_to, smtp_starttls) -> NotifySettings`, raising `ConfigError` if `smtp_host`/`smtp_from`/`smtp_to` are missing. `smtp_port` defaults to `25` when `None`. Used by `cli.py` (Task 11) and constructed directly in `notify.py`'s tests (Task 10).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
from gitlab_release.config import NotifySettings, load_notify_settings


def test_load_notify_settings_success() -> None:
    settings = load_notify_settings(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user=None,
        smtp_password=None,
        smtp_from="releases@example.com",
        smtp_to="team@example.com",
        smtp_starttls=False,
    )
    assert settings.smtp_host == "smtp.example.com"
    assert settings.smtp_port == 587
    assert settings.smtp_starttls is False


def test_load_notify_settings_defaults_port_to_25() -> None:
    settings = load_notify_settings(
        smtp_host="smtp.example.com",
        smtp_port=None,
        smtp_user=None,
        smtp_password=None,
        smtp_from="releases@example.com",
        smtp_to="team@example.com",
        smtp_starttls=False,
    )
    assert settings.smtp_port == 25


def test_load_notify_settings_missing_fields_raises_aggregated_config_error() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_notify_settings(
            smtp_host=None,
            smtp_port=None,
            smtp_user=None,
            smtp_password=None,
            smtp_from=None,
            smtp_to="team@example.com",
            smtp_starttls=False,
        )
    message = exc_info.value.message
    assert "smtp_host" in message
    assert "smtp_from" in message
    assert "smtp_to" not in message


def test_notify_settings_repr_masks_password() -> None:
    settings = load_notify_settings(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="u",
        smtp_password="s3cr3t",
        smtp_from="releases@example.com",
        smtp_to="team@example.com",
        smtp_starttls=False,
    )
    text = repr(settings)
    assert "s3cr3t" not in text
    assert "***MASKED***" in text
    assert "smtp.example.com" in text
```

(`pytest` and `ConfigError` are already imported at the top of `tests/test_config.py` from the first slice.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k notify -v`
Expected: FAIL with `ImportError: cannot import name 'NotifySettings'`.

- [ ] **Step 3: Implement `NotifySettings` and `load_notify_settings`**

Append to `src/gitlab_release/config.py`:

```python
@dataclass(frozen=True, repr=False)
class NotifySettings:
    smtp_host: str
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_from: str
    smtp_to: str
    smtp_starttls: bool

    _SECRET_FIELDS: ClassVar[frozenset[str]] = frozenset({"smtp_password"})

    def __repr__(self) -> str:
        parts = [f"{f.name}={self._masked_value(f.name)!r}" for f in fields(self)]
        return f"NotifySettings({', '.join(parts)})"

    def _masked_value(self, name: str) -> object:
        return "***MASKED***" if name in self._SECRET_FIELDS else getattr(self, name)

    def secret_values(self) -> tuple[str, ...]:
        return tuple(getattr(self, name) for name in self._SECRET_FIELDS if getattr(self, name))


def load_notify_settings(
    *,
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_user: str | None,
    smtp_password: str | None,
    smtp_from: str | None,
    smtp_to: str | None,
    smtp_starttls: bool,
) -> NotifySettings:
    """Validated independently from `load_settings`: --notify is opt-in, so these
    fields are only required when the flag is passed. cli.py calls this (if at all)
    before constructing GitlabClient, so a missing SMTP field still fails before any
    network call - same "fail fast, up front" guarantee, just a separate aggregated
    error from the GitLab-config one.
    """
    missing = []
    if not smtp_host:
        missing.append("smtp_host (set --smtp-host or SMTP_HOST)")
    if not smtp_from:
        missing.append("smtp_from (set --smtp-from or SMTP_FROM)")
    if not smtp_to:
        missing.append("smtp_to (set --smtp-to or SMTP_TO)")
    if missing:
        raise ConfigError(
            "Missing required notification configuration:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    assert smtp_host and smtp_from and smtp_to
    return NotifySettings(
        smtp_host=smtp_host,
        smtp_port=smtp_port or 25,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        smtp_from=smtp_from,
        smtp_to=smtp_to,
        smtp_starttls=smtp_starttls,
    )
```

This is a pure addition — `Settings`, `load_settings`, and all existing imports (`ClassVar`, `dataclass`, `fields`, `ConfigError`) are already present in the file from the first slice.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: all pass, including the original `Settings` tests (unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/gitlab_release/config.py tests/test_config.py
git commit -m "feat: add NotifySettings for SMTP configuration"
```

---

### Task 3: `gitlab_client.py` — construction and `previous_tag`

**Files:**
- Create: `src/gitlab_release/gitlab_client.py`
- Create: `tests/gitlab_fixtures.py`
- Test: `tests/test_gitlab_client.py`

**Interfaces:**
- Consumes: `gitlab_release.errors.GitLabAPIError` (existing).
- Produces: `RawCommit` (frozen dataclass: `sha`, `title`, `message`, `author_name`, `author_email` — all `str`, in that positional order); `GitlabClient(*, url: str, project_id: str, token: str, is_job_token: bool = False, ca_bundle: Path | None = None)`; `GitlabClient.previous_tag(self, *, before: str) -> str | None`. `tests/gitlab_fixtures.py` exposes `BASE_URL`, `API`, `PROJECT_ID`, `register_project()`, `register_tags(tags)`, `register_compare(commits)`, `register_commit_merge_requests(sha, mrs)`, `register_mr(iid)`, `register_mr_approvals(iid, approved_by=None, status=200)`, `register_empty_release(tag="v1.2.3")` — reused by Tasks 4, 5, and 11.

- [ ] **Step 1: Write the shared test fixtures module**

Create `tests/gitlab_fixtures.py`:

```python
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
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_gitlab_client.py`:

```python
import responses

from gitlab_release.gitlab_client import GitlabClient
from tests.gitlab_fixtures import PROJECT_ID, register_project, register_tags


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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_gitlab_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gitlab_release.gitlab_client'`.

- [ ] **Step 4: Implement `gitlab_client.py`**

Create `src/gitlab_release/gitlab_client.py`:

```python
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
        auth_kwargs = {"job_token": token} if is_job_token else {"private_token": token}
        self._gl = gitlab.Gitlab(
            url, ssl_verify=str(ca_bundle) if ca_bundle else True, **auth_kwargs
        )
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
        raise NotImplementedError  # Task 4

    def mr_approvers(self, commit_shas: Sequence[str]) -> dict[str, list[str]]:
        raise NotImplementedError  # Task 5
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_gitlab_client.py -v`
Expected: all 3 pass.

- [ ] **Step 6: Commit**

```bash
git add src/gitlab_release/gitlab_client.py tests/gitlab_fixtures.py tests/test_gitlab_client.py
git commit -m "feat: add GitlabClient with previous_tag resolution"
```

---

### Task 4: `gitlab_client.py` — `compare_commits`

**Files:**
- Modify: `src/gitlab_release/gitlab_client.py`
- Test: `tests/test_gitlab_client.py`

**Interfaces:**
- Consumes: `RawCommit`, `register_compare` (from Task 3).
- Produces: `GitlabClient.compare_commits` fully implemented — used by `changelog.build_context` (Task 8).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gitlab_client.py`:

```python
from tests.gitlab_fixtures import register_compare


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gitlab_client.py -k compare_commits -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement `compare_commits`**

In `src/gitlab_release/gitlab_client.py`, replace the `compare_commits` body:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gitlab_client.py -v`
Expected: all 5 pass.

- [ ] **Step 5: Commit**

```bash
git add src/gitlab_release/gitlab_client.py tests/test_gitlab_client.py
git commit -m "feat: implement GitlabClient.compare_commits"
```

---

### Task 5: `gitlab_client.py` — `mr_approvers`

**Files:**
- Modify: `src/gitlab_release/gitlab_client.py`
- Test: `tests/test_gitlab_client.py`

**Interfaces:**
- Consumes: `register_commit_merge_requests`, `register_mr`, `register_mr_approvals` (from Task 3).
- Produces: `GitlabClient.mr_approvers` fully implemented — used by `changelog.build_context` (Task 8).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gitlab_client.py`:

```python
from tests.gitlab_fixtures import (
    register_commit_merge_requests,
    register_mr,
    register_mr_approvals,
)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gitlab_client.py -k mr_approvers -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement `mr_approvers`**

In `src/gitlab_release/gitlab_client.py`, replace the `mr_approvers` body:

```python
def mr_approvers(self, commit_shas: Sequence[str]) -> dict[str, list[str]]:
    approvers: dict[str, list[str]] = {}
    for sha in commit_shas:
        try:
            commit = self._project.commits.get(sha)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gitlab_client.py -v`
Expected: all 8 pass.

- [ ] **Step 5: Commit**

```bash
git add src/gitlab_release/gitlab_client.py tests/test_gitlab_client.py
git commit -m "feat: implement GitlabClient.mr_approvers with 403/404 degrade"
```

---

### Task 6: `changelog.py` — `parse_commit` and `group_commits_by_type`

**Files:**
- Create: `src/gitlab_release/changelog.py`
- Test: `tests/test_changelog.py`

**Interfaces:**
- Consumes: `RawCommit` (Task 3).
- Produces: `ParsedCommit` (frozen dataclass: `sha`, `type`, `scope: str | None`, `subject`, `body`, `author_name`, `author_email`); `parse_commit(raw: RawCommit) -> ParsedCommit`; `group_commits_by_type(commits: list[ParsedCommit]) -> dict[str, list[ParsedCommit]]` (insertion order, `"other"` always last if present). Used by Tasks 7-9.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_changelog.py`:

```python
from gitlab_release.changelog import group_commits_by_type, parse_commit
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_changelog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gitlab_release.changelog'`.

- [ ] **Step 3: Implement `parse_commit` and `group_commits_by_type`**

Create `src/gitlab_release/changelog.py`:

```python
"""Commit/contributor/approver collection and Jinja2 rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass

from gitlab_release.gitlab_client import RawCommit

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_changelog.py -v`
Expected: all 3 pass.

- [ ] **Step 5: Commit**

```bash
git add src/gitlab_release/changelog.py tests/test_changelog.py
git commit -m "feat: add Conventional Commit parsing and grouping"
```

---

### Task 7: `changelog.py` — `dedupe_contributors`

**Files:**
- Modify: `src/gitlab_release/changelog.py`
- Test: `tests/test_changelog.py`

**Interfaces:**
- Consumes: `ParsedCommit`, `parse_commit` (Task 6).
- Produces: `Contributor` (frozen dataclass: `name: str`, `email: str`); `dedupe_contributors(commits: list[ParsedCommit]) -> list[Contributor]`. Used by `build_context` (Task 8).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_changelog.py`:

```python
from gitlab_release.changelog import Contributor, dedupe_contributors


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_changelog.py -k dedupe -v`
Expected: FAIL with `ImportError: cannot import name 'Contributor'`.

- [ ] **Step 3: Implement `Contributor` and `dedupe_contributors`**

Append to `src/gitlab_release/changelog.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_changelog.py -v`
Expected: all 6 pass.

- [ ] **Step 5: Commit**

```bash
git add src/gitlab_release/changelog.py tests/test_changelog.py
git commit -m "feat: add contributor dedup by email then name"
```

---

### Task 8: `changelog.py` — `ChangelogContext` and `build_context`

**Files:**
- Modify: `src/gitlab_release/changelog.py`
- Test: `tests/test_changelog.py`

**Interfaces:**
- Consumes: `GitlabClient` interface (`previous_tag`, `compare_commits`, `mr_approvers` — Tasks 3-5), `config.Settings` (existing), `parse_commit`, `dedupe_contributors`.
- Produces: `ChangelogContext` (frozen dataclass: `tag`, `previous_tag: str | None`, `project`, `released_at`, `commits: list[ParsedCommit]`, `contributors: list[Contributor]`, `approvers: list[str]`, `packages: list[dict[str, str]]`); `build_context(client, settings: Settings, packages: list[dict[str, str]] | None = None) -> ChangelogContext`. Used by `cli.py` (Task 11) and `render` (Task 9).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_changelog.py`:

```python
from gitlab_release.changelog import ChangelogContext, build_context
from gitlab_release.config import Settings


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

    context = build_context(client, settings)

    assert context.tag == "v1.0.0"
    assert context.previous_tag == "v0.9.0"
    assert context.project == "42"
    assert len(context.commits) == 2
    assert {c.name for c in context.contributors} == {"Alice", "Bob"}
    assert context.approvers == ["Carol"]
    assert context.packages == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_changelog.py -k build_context -v`
Expected: FAIL with `ImportError: cannot import name 'ChangelogContext'`.

- [ ] **Step 3: Implement `ChangelogContext` and `build_context`**

Append to `src/gitlab_release/changelog.py` (add `from datetime import UTC, datetime` and `from typing import TYPE_CHECKING` to the imports at the top; add `if TYPE_CHECKING: from gitlab_release.config import Settings; from gitlab_release.gitlab_client import GitlabClient` to avoid a runtime circular import, since `config.py` never imports `changelog.py` so this is one-directional but keeping it under `TYPE_CHECKING` costs nothing and matches how `gitlab_client.GitlabClient` is only used as a type hint here):

```python
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
    raw_commits = client.compare_commits(from_=previous_tag, to=settings.tag)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_changelog.py -v`
Expected: all 7 pass.

- [ ] **Step 5: Commit**

```bash
git add src/gitlab_release/changelog.py tests/test_changelog.py
git commit -m "feat: add ChangelogContext orchestration via build_context"
```

---

### Task 9: `templates/changelog.md.j2` and `changelog.render`

**Files:**
- Create: `src/gitlab_release/templates/changelog.md.j2`
- Modify: `src/gitlab_release/changelog.py`
- Test: `tests/test_changelog.py`

**Interfaces:**
- Consumes: `ChangelogContext`, `group_commits_by_type` (Tasks 6, 8); `gitlab_release.errors.TemplateError` (existing).
- Produces: `render(context: ChangelogContext, template_path: Path | None = None) -> str`. Used by `cli.py` (Task 11).

- [ ] **Step 1: Create the bundled default template**

Create `src/gitlab_release/templates/changelog.md.j2`:

```
# {{ tag }}

Released {{ released_at }}
{% if previous_tag %}
Changes since {{ previous_tag }}.
{% endif %}
{% for type, group in commits_by_type.items() %}
## {{ type }}

{% for commit in group %}
- {{ commit.subject }} ({{ commit.sha[:8] }})
{% endfor %}
{% endfor %}
{% if contributors %}
## Contributors

{% for contributor in contributors %}
- {{ contributor.name }}
{% endfor %}
{% endif %}
{% if approvers %}
## Approvers

{% for approver in approvers %}
- {{ approver }}
{% endfor %}
{% endif %}
{% if packages %}
## Packages

{% for package in packages %}
- [{{ package.name }}]({{ package.url }})
{% endfor %}
{% endif %}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_changelog.py` (add `from pathlib import Path` and `import pytest` to the top of the file):

```python
from gitlab_release.changelog import render
from gitlab_release.errors import TemplateError


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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_changelog.py -k render -v`
Expected: FAIL with `ImportError: cannot import name 'render'`.

- [ ] **Step 4: Implement `render`**

Add to the top of `src/gitlab_release/changelog.py`'s imports:

```python
from pathlib import Path

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from gitlab_release.errors import TemplateError
```

Append to `src/gitlab_release/changelog.py`:

```python
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
        loader=loader, trim_blocks=True, lstrip_blocks=True, undefined=StrictUndefined
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_changelog.py -v`
Expected: all 11 pass. If the ordering or section-presence assertions fail due to template whitespace specifics, adjust the template's blank lines (not the test's structural assertions) until they pass — the tests intentionally check structure/content/order rather than exact whitespace, so the template can be tuned freely.

- [ ] **Step 6: Commit**

```bash
git add src/gitlab_release/changelog.py src/gitlab_release/templates/changelog.md.j2 tests/test_changelog.py
git commit -m "feat: add bundled changelog template and sandboxed rendering"
```

---

### Task 10: `notify.py` — `send_notification`

**Files:**
- Create: `src/gitlab_release/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `config.NotifySettings` (Task 2).
- Produces: `send_notification(settings: NotifySettings, *, subject: str, markdown_body: str) -> None` — never raises. Not called from `cli.py` in this slice (documented seam for a future mutating-release slice).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_notify.py`:

```python
from unittest.mock import patch

from loguru import logger

from gitlab_release.config import NotifySettings
from gitlab_release.notify import send_notification


def _settings(**overrides: object) -> NotifySettings:
    defaults: dict[str, object] = dict(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user=None,
        smtp_password=None,
        smtp_from="releases@example.com",
        smtp_to="team@example.com",
        smtp_starttls=False,
    )
    defaults.update(overrides)
    return NotifySettings(**defaults)  # type: ignore[arg-type]


def test_send_notification_sends_multipart_email() -> None:
    with patch("gitlab_release.notify.smtplib.SMTP") as smtp_cls:
        smtp = smtp_cls.return_value.__enter__.return_value
        send_notification(_settings(), subject="Release v1.0.0", markdown_body="# v1.0.0\n\nStuff.")

    smtp.send_message.assert_called_once()
    sent = smtp.send_message.call_args[0][0]
    assert sent["Subject"] == "Release v1.0.0"
    assert sent["From"] == "releases@example.com"
    assert sent["To"] == "team@example.com"


def test_send_notification_uses_starttls_when_configured() -> None:
    with patch("gitlab_release.notify.smtplib.SMTP") as smtp_cls:
        smtp = smtp_cls.return_value.__enter__.return_value
        send_notification(_settings(smtp_starttls=True), subject="s", markdown_body="b")

    smtp.starttls.assert_called_once()


def test_send_notification_skips_login_for_unauthenticated_relay() -> None:
    with patch("gitlab_release.notify.smtplib.SMTP") as smtp_cls:
        smtp = smtp_cls.return_value.__enter__.return_value
        send_notification(_settings(), subject="s", markdown_body="b")

    smtp.login.assert_not_called()


def test_send_notification_logs_in_when_credentials_present() -> None:
    with patch("gitlab_release.notify.smtplib.SMTP") as smtp_cls:
        smtp = smtp_cls.return_value.__enter__.return_value
        send_notification(
            _settings(smtp_user="user", smtp_password="pass"), subject="s", markdown_body="b"
        )

    smtp.login.assert_called_once_with("user", "pass")


def test_send_notification_connection_failure_is_non_fatal() -> None:
    with patch("gitlab_release.notify.smtplib.SMTP", side_effect=ConnectionRefusedError("refused")):
        send_notification(_settings(), subject="s", markdown_body="b")  # must not raise


def test_send_notification_logs_warning_on_failure() -> None:
    captured = []
    sink_id = logger.add(captured.append, level=0)
    try:
        with patch(
            "gitlab_release.notify.smtplib.SMTP", side_effect=ConnectionRefusedError("refused")
        ):
            send_notification(_settings(), subject="s", markdown_body="b")
    finally:
        logger.remove(sink_id)

    assert any(r.record["level"].name == "WARNING" for r in captured)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gitlab_release.notify'`.

- [ ] **Step 3: Implement `send_notification`**

Create `src/gitlab_release/notify.py`:

```python
"""SMTP notification. Optional and non-fatal by definition."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from loguru import logger

from gitlab_release.config import NotifySettings


def send_notification(settings: NotifySettings, *, subject: str, markdown_body: str) -> None:
    """Never raises: a notification failure must not fail a job whose release already
    succeeded. Failures are logged as a warning and swallowed."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = settings.smtp_to
    message.set_content(markdown_body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except Exception as exc:  # noqa: BLE001 - notification failure must never fail the job
        logger.warning("Failed to send notification email: {}", exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_notify.py -v`
Expected: all 6 pass.

- [ ] **Step 5: Commit**

```bash
git add src/gitlab_release/notify.py tests/test_notify.py
git commit -m "feat: add non-fatal SMTP notification sender"
```

---

### Task 11: `cli.py` — wire changelog generation and notify preview into `release`

**Files:**
- Modify: `src/gitlab_release/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `gitlab_client.GitlabClient` (Tasks 3-5), `changelog.build_context`/`render` (Tasks 8-9), `config.NotifySettings`/`load_notify_settings` (Task 2), `tests/gitlab_fixtures.register_empty_release` (Task 3).
- Produces: `release` command gains `--template`, `--notify/--no-notify`, `--smtp-host`, `--smtp-port`, `--smtp-user`, `--smtp-password`, `--smtp-from`, `--smtp-to`, `--smtp-starttls/--no-smtp-starttls`. `_run_release` signature changes (internal only, no external consumers).

- [ ] **Step 1: Write the failing/updated tests**

Replace the full contents of `tests/test_cli.py` with:

```python
import json
from unittest.mock import patch

import responses
from click.testing import CliRunner

from gitlab_release.cli import cli
from tests.gitlab_fixtures import register_empty_release

VALID_ENV = {
    "CI_SERVER_URL": "https://gitlab.example.com",
    "CI_PROJECT_ID": "42",
    "CI_JOB_TOKEN": "job-token-sentinel",
    "CI_COMMIT_TAG": "v1.2.3",
    "CI_COMMIT_SHA": "abc123",
}


@responses.activate
def test_dry_run_success_prints_summary_without_token() -> None:
    register_empty_release(tag="v1.2.3")

    runner = CliRunner()
    result = runner.invoke(cli, ["release"], env=VALID_ENV)

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "v1.2.3" in result.stdout
    assert "42" in result.stdout
    assert "https://gitlab.example.com" in result.stdout
    assert "job-token-sentinel" not in result.stdout


@responses.activate
def test_dry_run_json_output_is_valid_json_without_token() -> None:
    register_empty_release(tag="v1.2.3")

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "release"], env=VALID_ENV)

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["tag"] == "v1.2.3"
    assert "changelog" in data
    assert "token" not in data


def test_missing_required_var_exits_2_names_field() -> None:
    env = dict(VALID_ENV)
    del env["CI_COMMIT_TAG"]

    runner = CliRunner()
    result = runner.invoke(cli, ["release"], env=env)

    assert result.exit_code == 2
    assert "tag" in result.stderr
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


def test_no_dry_run_rejected() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["release", "--no-dry-run"], env=VALID_ENV)

    assert result.exit_code == 2
    assert "not implemented" in result.stderr.lower()


def test_verbose_and_quiet_together_rejected() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--verbose", "--quiet", "release"], env=VALID_ENV)

    assert result.exit_code == 2


@responses.activate
def test_unexpected_error_exits_1_with_clean_message(monkeypatch) -> None:
    register_empty_release(tag="v1.2.3")

    def boom(*, settings, changelog_text, notify_settings, json_output):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("gitlab_release.cli._run_release", boom)

    runner = CliRunner()
    result = runner.invoke(cli, ["release"], env=VALID_ENV)

    assert result.exit_code == 1
    assert "kaboom" in result.stderr
    assert "Traceback" not in result.stderr


@responses.activate
def test_secret_redacted_in_verbose_logs() -> None:
    register_empty_release(tag="v1.2.3")

    runner = CliRunner()
    result = runner.invoke(cli, ["--verbose", "release"], env=VALID_ENV)

    assert result.exit_code == 0, result.stderr
    assert "job-token-sentinel" not in result.stderr


@responses.activate
def test_notify_preview_under_dry_run_does_not_send_email() -> None:
    register_empty_release(tag="v1.2.3")

    with patch("gitlab_release.notify.smtplib.SMTP") as smtp_cls:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "release",
                "--notify",
                "--smtp-host",
                "smtp.example.com",
                "--smtp-from",
                "releases@example.com",
                "--smtp-to",
                "team@example.com",
            ],
            env=VALID_ENV,
        )

    assert result.exit_code == 0, result.stderr
    assert "Would send notification to team@example.com" in result.stdout
    smtp_cls.assert_not_called()


def test_notify_missing_smtp_config_exits_2() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["release", "--notify"], env=VALID_ENV)

    assert result.exit_code == 2
    assert "smtp_host" in result.stderr
```

(`gitlab_release.notify` is imported only inside the test via the patch target string — `cli.py` itself does not import `notify` in this slice, matching the seam design. `smtp_cls.assert_not_called()` still works as a patch target because `unittest.mock.patch` resolves `"gitlab_release.notify.smtplib.SMTP"` by importing `gitlab_release.notify` itself, independent of whether `cli.py` imports it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: multiple FAILs — `test_dry_run_success_prints_summary_without_token` fails because `responses` has registered mocks that are never called (old `cli.py` doesn't hit the network yet) or `data["changelog"]` `KeyError`; `test_notify_preview_under_dry_run_does_not_send_email` and `test_notify_missing_smtp_config_exits_2` fail with `click.exceptions.NoSuchOption` for `--notify`. This confirms the new behavior doesn't exist yet.

- [ ] **Step 3: Update `cli.py`**

In `src/gitlab_release/cli.py`, update the imports (add to the existing `from gitlab_release import config` / `from gitlab_release import logging as log_setup` block):

```python
from gitlab_release import changelog, config, gitlab_client
from gitlab_release import logging as log_setup
```

Replace the `release` command's option stack and function signature with:

```python
@cli.command()
@click.option(
    "--gitlab-url",
    envvar="GITLAB_URL",
    default=None,
    help="GitLab instance URL. Falls back to CI_SERVER_URL.",
)
@click.option(
    "--project-id",
    envvar="GITLAB_PROJECT_ID",
    default=None,
    help="Project ID or path. Falls back to CI_PROJECT_ID.",
)
@click.option(
    "--token",
    envvar="GITLAB_TOKEN",
    default=None,
    help="API token. Falls back to CI_JOB_TOKEN (cannot create tags).",
)
@click.option(
    "--tag",
    envvar="RELEASE_TAG",
    default=None,
    help="Release tag. Falls back to CI_COMMIT_TAG.",
)
@click.option(
    "--ca-bundle",
    envvar="REQUESTS_CA_BUNDLE",
    default=None,
    type=click.Path(path_type=Path, dir_okay=False),
    help="CA bundle for self-hosted instances.",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=True,
    envvar="RELEASE_DRY_RUN",
    help="Preview only; --no-dry-run is reserved for a future release.",
)
@click.option(
    "--template",
    "template_path",
    envvar="RELEASE_TEMPLATE",
    default=None,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Override the bundled changelog template.",
)
@click.option(
    "--notify/--no-notify",
    default=False,
    envvar="RELEASE_NOTIFY",
    help="Preview an SMTP notification (sending is not implemented yet).",
)
@click.option("--smtp-host", envvar="SMTP_HOST", default=None)
@click.option("--smtp-port", envvar="SMTP_PORT", default=None, type=int)
@click.option("--smtp-user", envvar="SMTP_USER", default=None)
@click.option("--smtp-password", envvar="SMTP_PASSWORD", default=None)
@click.option("--smtp-from", envvar="SMTP_FROM", default=None)
@click.option("--smtp-to", envvar="SMTP_TO", default=None)
@click.option(
    "--smtp-starttls/--no-smtp-starttls",
    envvar="SMTP_STARTTLS",
    default=False,
)
@handle_errors
def release(
    gitlab_url: str | None,
    project_id: str | None,
    token: str | None,
    tag: str | None,
    ca_bundle: Path | None,
    dry_run: bool,
    template_path: Path | None,
    notify: bool,
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_user: str | None,
    smtp_password: str | None,
    smtp_from: str | None,
    smtp_to: str | None,
    smtp_starttls: bool,
) -> None:
    """Show what a release would do, without creating anything."""
    ctx = click.get_current_context()
    assert ctx is not None
    run_ctx: RunContext = ctx.obj

    settings = config.load_settings(
        gitlab_url=gitlab_url,
        project_id=project_id,
        token=token,
        tag=tag,
        ca_bundle=ca_bundle,
    )
    secrets = list(settings.secret_values())

    notify_settings: config.NotifySettings | None = None
    if notify:
        notify_settings = config.load_notify_settings(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            smtp_from=smtp_from,
            smtp_to=smtp_to,
            smtp_starttls=smtp_starttls,
        )
        secrets.extend(notify_settings.secret_values())

    log_setup.configure_logging(
        level=run_ctx.level,
        json_output=run_ctx.json_output,
        verbose=run_ctx.verbose,
        secrets=secrets,
    )

    if not dry_run:
        raise ConfigError(
            "--no-dry-run is not supported yet: release creation, artifact upload, "
            "and notification sending are not implemented in this build. "
            "Only --dry-run is available."
        )

    client = gitlab_client.GitlabClient(
        url=settings.gitlab_url,
        project_id=settings.project_id,
        token=settings.token,
        is_job_token=settings.is_job_token,
        ca_bundle=settings.ca_bundle,
    )
    context = changelog.build_context(client, settings)
    changelog_text = changelog.render(context, template_path=template_path)

    _run_release(
        settings=settings,
        changelog_text=changelog_text,
        notify_settings=notify_settings,
        json_output=run_ctx.json_output,
    )
```

Replace `_run_release` with:

```python
def _run_release(
    *,
    settings: config.Settings,
    changelog_text: str,
    notify_settings: config.NotifySettings | None,
    json_output: bool,
) -> None:
    """Business logic seam - no click objects below this line. Currently prints a
    projection of the validated config plus a real, GitLab-sourced changelog. Does not
    call notify.send_notification: dry-run is still the only supported mode, so the
    notification is always previewed, never sent.
    """
    logger.debug("Building dry-run summary for tag={}", settings.tag)
    subject = f"Release {settings.tag}"
    summary: dict[str, Any] = {
        "dry_run": True,
        "gitlab_url": settings.gitlab_url,
        "project_id": settings.project_id,
        "tag": settings.tag,
        "ref": settings.ref,
        "changelog": changelog_text,
    }
    if notify_settings is not None:
        summary["notify_preview"] = {"to": notify_settings.smtp_to, "subject": subject}

    if json_output:
        click.echo(json.dumps(summary))
        return

    click.echo(
        f"[dry-run] Would create release for tag {settings.tag!r} at ref "
        f"{settings.ref!r} on project {settings.project_id!r} "
        f"({settings.gitlab_url}). No changes made."
    )
    click.echo("\n--- Changelog preview ---")
    click.echo(changelog_text)
    if notify_settings is not None:
        click.echo(
            f"[dry-run] Would send notification to {notify_settings.smtp_to} "
            f'with subject "{subject}". No email sent.'
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all 9 pass.

- [ ] **Step 5: Commit**

```bash
git add src/gitlab_release/cli.py tests/test_cli.py
git commit -m "feat: wire changelog generation and notify preview into release"
```

---

### Task 12: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest -q`
Expected: all tests pass (first-slice tests + all tests added in Tasks 1-11), no warnings.

- [ ] **Step 2: Lint and format**

Run: `uv run ruff check --fix . && uv run ruff format .`
Expected: clean. If `ruff format` reformats any file, re-run `uv run pytest -q` to confirm nothing broke, then amend the affected task's commit is not necessary — just stage and commit the formatting fix separately.

- [ ] **Step 3: Type check**

Run: `uv run mypy src/`
Expected: `Success: no issues found in N source files`. If `gitlab.*` stub errors appear despite the Task 1 override, check the override's `module` glob matches the actual import paths used (`gitlab`, `gitlab.exceptions`) and add a second override block for `gitlab.exceptions.*` if needed.

- [ ] **Step 4: Manual smoke test**

```bash
GITLAB_TOKEN=x CI_SERVER_URL=https://gitlab.example.com CI_PROJECT_ID=1 \
  CI_COMMIT_TAG=v1.0.0 CI_COMMIT_SHA=abc123 \
  uv run gitlab-release release --notify \
  --smtp-host smtp.example.com --smtp-from releases@example.com --smtp-to team@example.com
```

Expected: exits 2 with a `GitLabAPIError`-flavored network error (there's no real `gitlab.example.com` to reach) — this is expected outside a real GitLab instance; the point of this manual step is to confirm the CLI *attempts* the real API call rather than skipping it, and that `--notify` didn't blow up option parsing. To manually verify the full happy path without live GitLab access, rely on the `test_cli.py` suite from Task 11 instead, which mocks the API.

- [ ] **Step 5: Commit any formatting fixes**

```bash
git add -A
git commit -m "chore: ruff format fixes"
```

(Skip this step if Step 2 made no changes.)

---

## Self-Review Notes

- **Spec coverage:** `gitlab_client.py` (Tasks 3-5) ✅, `changelog.py` collection+dedup+grouping+rendering (Tasks 6-9) ✅, bundled template (Task 9) ✅, `notify.py` (Task 10) ✅, `NotifySettings`/validation (Task 2) ✅, `cli.py` wiring incl. dry-run notify preview (Task 11) ✅, dependency approval (Task 1) ✅. Out-of-scope items from the spec (tag/release/artifact mutation, `--if-exists`, retries) are intentionally absent from every task.
- **Type consistency checked:** `RawCommit` field order (Task 3) matches every positional construction in Tasks 6-9; `GitlabClient` method signatures (Tasks 3-5) match `build_context`'s calls (Task 8) and the `_FakeGitlabClient` test double's shape (Task 8); `ChangelogContext` fields (Task 8) match `render`'s usage (Task 9); `NotifySettings` fields (Task 2) match `notify.py` (Task 10) and `cli.py`'s `load_notify_settings` call (Task 11); `_run_release`'s new signature (Task 11) matches both its call site and the monkeypatched `boom` test double's signature.
- **No placeholders:** every step has real code; the one flagged uncertainty (exact template whitespace, Task 9 Step 5) is scoped to a specific, bounded adjustment with a clear rule ("tune the template, not the test"), not an open-ended TODO.
