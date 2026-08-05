# CLAUDE.md

Project guidance for Claude Code. Read this before making changes.

## What this is

A Python CLI that runs **inside a Docker container as a GitLab CI/CD job**. It automates the
release step of a pipeline:

1. Create a git tag and a GitLab Release for the current commit.
2. Upload build artifacts (RPMs, tarballs, zips) from a source path to the project's
   **Generic Package Registry**.
3. Generate a changelog from commits, contributors, and MR approvers via Jinja2 templates.
4. Attach package links to the Release.
5. Optionally send an SMTP email notification announcing the release.

**Primary consumer is a CI runner, not a human at a terminal.** Every design decision follows
from that: no interactive prompts, no TTY assumptions, deterministic output, meaningful exit
codes, and secrets that never reach stdout.

## Stack

| Concern | Library | Notes |
|---|---|---|
| CLI framework | `rich-click` | Click under the hood; rich formatting for `--help` |
| GitLab API | `python-gitlab` | Never hand-roll `requests` calls against the API |
| Templating | `jinja2` | Sandboxed environment, autoescape off (we emit Markdown) |
| Config | `python-dotenv` | `.env` for local dev only; CI uses real env vars |
| Logging | `loguru` | Single sink configured in `cli.py`; never `logging` |
| Email | `smtplib` + `email.message` | Stdlib only, no extra dependency |
| Packaging/deps | `uv` | `uv.lock` is committed and authoritative |
| Lint/format | `ruff` | Both linter and formatter |
| Types | `mypy --strict` | New modules must pass strict mode |
| Tests | `pytest` + `responses` | Never hit a live GitLab instance in tests |

Python 3.11+. Do not add a dependency without asking first — the container image is deliberately
small, and this may be deployed to a self-hosted/air-gapped GitLab where wheels must be vendored.

## Layout

```
src/gitlab_release/
  __main__.py         # python -m gitlab_release
  cli.py              # rich-click group + commands; argument parsing ONLY
  config.py           # Settings dataclass, env resolution, validation
  gitlab_client.py    # thin wrapper over python-gitlab; the only module that talks to the API
  changelog.py        # commit/contributor/approver collection + Jinja2 rendering
  artifacts.py        # glob source path, validate, upload to package registry
  notify.py           # SMTP notification
  logging.py          # loguru sink setup, secret-redaction patcher
  errors.py           # exception hierarchy -> exit codes
  templates/
    changelog.md.j2   # default template
tests/
  fixtures/           # recorded API payloads
Dockerfile
pyproject.toml
```

**Layering rule:** `cli.py` parses and delegates. Business logic lives in the modules and must be
callable without Click. If a function needs a `click.Context` to work, it is in the wrong place.

## Commands

```bash
uv sync --all-extras                    # install
uv run gitlab-release --help
uv run pytest -q                        # tests
uv run ruff check --fix . && uv run ruff format .
uv run mypy src/

docker build -t gitlab-release:dev .
docker run --rm --env-file .env -v "$PWD/dist:/artifacts:ro" gitlab-release:dev \
  release --source-path /artifacts --dry-run
```

## Configuration

Precedence, highest first: **CLI flag → environment variable → `.env` file → default.**

`load_dotenv(override=False)` is called once, at the top of `cli.py`, before Click resolves
parameters. Every option gets an `envvar=` so Click handles the lookup itself — don't read
`os.environ` scattered through the codebase; `config.py` owns that.

GitLab's predefined CI variables are the defaults, so a job needs almost no explicit config:

| Setting | Env var | CI default |
|---|---|---|
| GitLab URL | `GITLAB_URL` | `CI_SERVER_URL` |
| Project ID | `GITLAB_PROJECT_ID` | `CI_PROJECT_ID` |
| Token | `GITLAB_TOKEN` | — (see below) |
| Job token | — | `CI_JOB_TOKEN` |
| Tag | `RELEASE_TAG` | `CI_COMMIT_TAG` |
| Ref/SHA | — | `CI_COMMIT_SHA` |
| CA bundle | `REQUESTS_CA_BUNDLE` | — (self-hosted instances) |

Validate configuration **up front** and fail with a single, complete error listing everything
that's missing. A CI job that dies twenty minutes in because SMTP creds were absent is a bug.

## GitLab integration

All API access goes through `gitlab_client.py`. Construct once, pass the client down.

**Token gotcha, encode this in error messages:** `CI_JOB_TOKEN` can create releases and upload
generic packages, but it **cannot create tags**. Tag creation requires a project access token or
PAT with `api` scope. If `--create-tag` is requested with only a job token, fail early with that
explanation rather than surfacing a bare 403.

Key calls:

```python
project.tags.create({"tag_name": tag, "ref": ref})
project.generic_packages.upload(
    package_name=name,
    package_version=version,
    file_name=path.name,
    path=path,
)
project.releases.create(
    {
        "name": name,
        "tag_name": tag,
        "description": changelog_md,
        "assets": {
            "links": [
                {"name": f.name, "url": f.url, "link_type": "package"},
            ]
        },
    }
)
project.repository_compare(from_=prev_tag, to=tag)  # commits in range
```

Conventions:

- **Idempotency.** Re-running a job must not explode. Tag/release/package already exists →
  respect `--if-exists {fail,skip,update}`, default `fail`. Retries happen; make them survivable.
- **Retries.** Wrap network calls with bounded exponential backoff on 5xx and 429 only. Never
  retry 4xx.
- **`--dry-run` is mandatory** on every mutating command. It must exercise the full read path and
  print exactly what would be created, without writing.
- Package version defaults to the tag with a leading `v` stripped; the registry rejects some
  characters, so normalize and say so if the tag is unusable.

## Changelog generation

Commit range is `previous tag → current tag`. Resolve the previous tag by walking the project's
tags sorted by committed date, not by name — semver sorting on strings will bite you.

Collected into the template context:

- `commits` — parsed subject/body, grouped by Conventional Commit type where present, with an
  `other` bucket for anything unparseable. Never drop a commit because it didn't parse.
- `contributors` — deduplicated by email, then by name; some people commit from two addresses.
- `approvers` — from merge requests associated with the commits
  (`mr.approvals.get().approved_by`). Approver data may be unavailable on Free tier or when the
  MR was merged without approval rules; degrade to an empty list, don't crash.
- `packages` — uploaded artifact names with registry URLs.
- `tag`, `previous_tag`, `project`, `released_at` (UTC, ISO 8601).

Templates: `--template PATH` overrides the bundled default. Use a `SandboxedEnvironment` with
`trim_blocks=True, lstrip_blocks=True`, and `undefined=StrictUndefined` so a typo in a custom
template fails loudly instead of rendering an empty section. Output is Markdown for the Release
description; the same rendered text is reused in the email body.

## Notifications

Optional and **non-fatal by definition.** Controlled by `--notify/--no-notify` (default off) plus
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TO`, `SMTP_STARTTLS`.

If sending fails, log a warning and exit successfully — the release already happened, and failing
the pipeline after a successful publish makes the job's exit status a lie. Send multipart with the
Markdown changelog as the plaintext part. Support unauthenticated relays: an internal SMTP host
with no credentials is a normal configuration, not an error.

## Error handling and exit codes

Exceptions derive from `ReleaseError` in `errors.py` and carry an exit code. `cli.py` has one
top-level handler that prints a clean message via rich-click and exits — no tracebacks in CI logs
unless `--verbose` is set.

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Unexpected/internal error |
| 2 | Usage or configuration error |
| 3 | GitLab API error (auth, permissions, 5xx after retries) |
| 4 | Artifact error (source path empty, unreadable, upload failed) |
| 5 | Template rendering error |

## Logging and secrets

`loguru` is the only logging library. Do not use stdlib `logging`, and do not add a second
logging abstraction on top of loguru — `from loguru import logger` at the top of the module is
the whole API.

Setup happens once, in `logging.py`, called from `cli.py` before anything else runs:

```python
logger.remove()  # kill the default stderr handler first
logger.add(
    sys.stderr,
    level=level,
    format=FMT,
    colorize=sys.stderr.isatty(),
    backtrace=False,
    diagnose=False,
)
```

- `logger.remove()` before adding a sink, always. Skipping it double-logs every line.
- **`diagnose=False` in CI.** Loguru's variable-value tracebacks are wonderful locally and a
  secret-leak vector in a job log — they print the contents of local variables, tokens included.
  Only enable `backtrace`/`diagnose` under `--verbose` when the run is interactive.
- Level from `--verbose`/`--quiet`: `DEBUG` / `INFO` / `WARNING`. `--json` switches the sink to
  `serialize=True`.
- Third-party libraries (`python-gitlab`, `urllib3`) still emit through stdlib `logging`.
  Intercept them into loguru with a single `InterceptHandler` in `logging.py` rather than
  configuring stdlib handlers separately.
- Redact secrets at the sink, not at the call site. Register a `logger.patch(...)` or a filter
  that scrubs known secret values from `record["message"]` and `record["extra"]`, so a leak can't
  happen just because someone forgot at one call site. `Settings.__repr__` also masks secret
  fields — if you add a secret field, add it to the mask list in the same commit.
- **Never log a token, password, or full `Authorization` header.** Not at debug level, not in an
  exception message. Don't echo `.env` contents or the resolved config without masking.
- Use `logger.bind(tag=..., job=...)` for context instead of f-string prefixes on every message.
- Split the streams: user-facing results go to **stdout** via rich-click; logs and diagnostics go
  to **stderr** via loguru. `--json` emits a machine-readable summary on stdout for downstream
  jobs to parse.
- Detect non-TTY and disable colors/spinners automatically — GitLab job logs render ANSI poorly
  and progress bars produce thousands of useless lines.
- In tests, capture with `caplog` via the intercept handler or add a list sink; don't assert on
  raw stderr text.

## Docker

Multi-stage: `uv` builds a venv in the builder stage, runtime stage copies it onto a slim base.
Run as a non-root user. `ENTRYPOINT` is the CLI, so the job's `script:` passes subcommands
directly. Keep the image small — this pulls on every pipeline run.

Artifacts are mounted read-only from a prior job's `artifacts:` directory. The tool must never
write to the source path.

## Testing

- Mock all HTTP with `responses`; fixtures live in `tests/fixtures/`. No live API calls, ever.
- Cover the awkward paths, not just the happy one: expired token, job token attempting tag
  creation, empty commit range, tag already exists, source path matching zero files, approver
  endpoint returning 403, SMTP connection refused.
- Changelog rendering gets snapshot tests against the default template.
- `--dry-run` needs an assertion that no mutating method was called.

## Style

- Type hints on every public function. `pathlib.Path`, never string paths.
- `dataclass(frozen=True)` for config and value objects.
- Docstrings explain *why*; the signature already says what.
- No `print()` in library modules — return data and let `cli.py` render it, or use `logger` for
  diagnostics.
- Keep `cli.py` thin enough to read in one sitting.

## Git conventions

- **Do not add `Co-authored-by:` trailers to commit messages.** No `Co-Authored-By: Claude`, no
  "Generated with Claude Code" footer, no tool attribution of any kind. Commit messages contain
  the change and nothing else.
- Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`) — the changelog
  groups on these, so the format is load-bearing here, not cosmetic.
- Imperative subject, under ~72 characters, no trailing period. Body explains *why* when the
  reason isn't obvious from the diff.
- Reference issues as `Closes #123` on its own line at the end.