# Changelog generation and email notification

## Context

The first vertical slice (`errors.py`, `logging.py`, `config.py`, `cli.py`) proved the
CLI's shape end-to-end with a `release --dry-run` command that validates config and
prints a summary, but touches no GitLab API and sends no email. This spec covers the
next slice: generating a real changelog from GitLab commit/contributor/approver data,
and sending (or, under dry-run, previewing) an SMTP notification — steps 3 and 5 of
the five-step release process in `CLAUDE.md`.

Tag creation, release creation, and artifact upload (steps 1, 2, 4) remain **out of
scope** and are deferred to a future slice, along with `--if-exists` idempotency and
retry/backoff logic.

## Decisions confirmed with the user

- **New dependencies approved**: `python-gitlab` and `jinja2`, per `CLAUDE.md`'s stack
  table — no alternative avoids them without reinventing GitLab API access or sandboxed
  templating from scratch.
- **Read-only for this slice**: `gitlab_client.py` grows only far enough to fetch
  commits, contributors, and MR approvers. No tag/release/artifact mutation yet.
- **Dry-run and email**: under `--dry-run` (still the only supported mode —
  `--no-dry-run` continues to be rejected with `ConfigError`, unchanged from the first
  slice), the notification is **previewed**, never actually sent. `notify.py`'s real
  `send_notification` is fully implemented and tested now, but `cli.py` has no path
  that calls it yet — same seam pattern as `gitlab_client.py` itself, which the first
  slice's design anticipated.

## `gitlab_client.py` (new)

A `GitlabClient` class wrapping a `gitlab.Gitlab` instance plus the resolved `Project`,
constructed once in `cli.py` and passed down (CLAUDE.md: "construct once, pass the
client down"). All GitLab API access goes through this module — no other module
imports `gitlab`.

Three methods, each a narrow, independently-testable seam:

- `previous_tag(before: str) -> str | None` — lists the project's tags, sorts by
  **committed date** (not name — semver-as-string sorting is explicitly wrong per
  CLAUDE.md), and returns the tag immediately before `before`. Returns `None` if
  `before` is the first tag in the project's history (changelog covers "everything up
  to this release").
- `compare_commits(from_: str | None, to: str) -> list[RawCommit]` — wraps
  `project.repository_compare`. `from_=None` means "from the beginning of history."
- `mr_approvers(commit_shas: Sequence[str]) -> dict[str, list[str]]` — for each commit,
  looks up associated merge requests and their `approvals.get().approved_by`. Catches
  403/404 **per MR lookup** and degrades that entry to `[]` rather than failing the
  whole changelog (CLAUDE.md: "degrade to an empty list, don't crash" — Free tier or an
  MR merged without approval rules are both normal, not errors).

All three raise `GitLabAPIError` (exit code 3) on unexpected failures (401, 5xx after
retries are exhausted elsewhere in the stack). No retry/backoff logic is added in this
module in this slice — flagged as deferred, matching the tag/release deferral.

## `changelog.py` (new)

- `build_context(client: GitlabClient, settings: Settings, packages: Sequence[Mapping[str, str]] = ()) -> ChangelogContext`
  (each package entry is `{"name": ..., "url": ...}` — the shape `artifacts.py` will
  produce once it exists; a plain mapping avoids inventing a dataclass for a type with
  no producer yet)
  (a frozen dataclass) — orchestrates the three `GitlabClient` calls and assembles:
  - **commits**: subject/body parsed against a Conventional Commit regex
    (`type(scope)?: subject`), grouped by type; anything unparseable goes into an
    `other` bucket. A commit is never dropped for failing to parse.
  - **contributors**: deduplicated first by email, then by display name (some people
    commit from two addresses).
  - **approvers**, **packages** (always `()` in this slice — `artifacts.py` doesn't
    exist yet), **tag**, **previous_tag**, **project**, **released_at**
    (`datetime.now(UTC).isoformat()`).
- `render(context: ChangelogContext, template_path: Path | None) -> str` — a
  `SandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, undefined=StrictUndefined)`.
  Loads the bundled `templates/changelog.md.j2` unless `--template PATH` overrides it.
  `StrictUndefined` turns a typo in a custom template into a `TemplateError` (exit 5)
  instead of a silently-blank section.
- `templates/changelog.md.j2` — the bundled default: commits grouped by Conventional
  Commit type (with an "other" section), a contributors list, an approvers list, and a
  packages section that must render sensibly even when `packages` is empty.

## `notify.py` (new)

`send_notification(settings: NotifySettings, subject: str, markdown_body: str) -> None`
builds a multipart email (plaintext part = the rendered changelog Markdown), connects
via `smtplib.SMTP`, uses STARTTLS only when `smtp_starttls` is set, and calls `.login()`
only when both `smtp_user` and `smtp_password` are present (an unauthenticated internal
relay is a normal configuration, not an error, per CLAUDE.md). **Catches every
exception, logs a warning, and returns normally — it never raises.** This is
non-negotiable per CLAUDE.md: "the release already happened, and failing the pipeline
after a successful publish makes the job's exit status a lie."

## `config.py` changes

- New `NotifySettings` frozen dataclass, constructed only when `--notify` is passed:
  `smtp_host`, `smtp_port`, `smtp_user: str | None`, `smtp_password: str | None`
  (secret — added to `_SECRET_FIELDS` alongside `token`), `smtp_from`, `smtp_to`,
  `smtp_starttls: bool`.
- Validation: if `--notify` is passed but `smtp_host`/`smtp_from`/`smtp_to` are
  missing, those join the *same* aggregated `ConfigError` as every other missing
  field — still one error, still fails before any GitLab call is made.

## `cli.py` changes

`release` gains `--notify/--no-notify` (default off) and `--template PATH`.
`_run_release` now additionally:

1. Constructs `GitlabClient` from `Settings`.
2. Calls `changelog.build_context` + `changelog.render`, and includes the rendered
   changelog in the dry-run summary (stdout text, or under the `changelog` key in
   `--json` output).
3. If `--notify`: since dry-run is still the only mode, prints
   `[dry-run] Would send notification to <smtp_to> with subject "<subject>"` — does
   **not** call `notify.send_notification`. The real call is wired in once
   `--no-dry-run` mutating support lands in a future slice.

## Testing

- **`gitlab_client.py`**: `responses`-mocked fixtures for tag listing sorted by commit
  date, `repository_compare`, commit→MR→approvals lookup, and a 403-on-approvals case
  that degrades to `[]` instead of raising.
- **`changelog.py`**: Conventional Commit parsing (one typed commit, one unparseable
  "other" commit), contributor dedup by email and by name, and a **snapshot test**
  rendering the bundled template against a fixed `ChangelogContext`.
- **`notify.py`**: successful send (mocked `smtplib.SMTP`), connection-refused → logs a
  warning and returns normally (does not raise), and an unauthenticated-relay case
  (no user/password → `.login()` is never called).
- **`cli.py`**: `--notify` with valid SMTP config under (the only supported) dry-run →
  asserts the preview line appears and that no SMTP connection is attempted (monkeypatch
  `smtplib.SMTP` to fail the test if constructed).

## Out of scope (deferred, not forgotten)

Tag creation, release creation, artifact upload (so `packages` stays empty in this
slice), `--if-exists` idempotency, retry/backoff on GitLab API calls, and actually
sending the notification email from the CLI (the module is real and tested; no CLI path
reaches it yet).
