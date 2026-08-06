# git-release

A Python CLI that runs inside a Docker container as a GitLab CI/CD job and automates the
release step of a pipeline: tagging, package upload, changelog generation, and email
notification. See [CLAUDE.md](CLAUDE.md) for the full target design.

**Current status:** the `release` command runs a full **read-only preview**. It validates
configuration, talks to the GitLab API to build a real changelog from commits, contributors,
and merge request approvers, and previews an SMTP notification — all without creating
anything. Tag creation, release creation, artifact upload, actually sending the notification,
and Docker packaging are not implemented yet.

## Install

```bash
uv sync --all-extras
```

## Usage

```bash
uv run gitlab-release release [OPTIONS]
```

Every setting can be passed as a `--flag`, an environment variable, or (for GitLab-related
settings) picked up automatically from GitLab's predefined CI variables — a job running in
GitLab CI needs little to no explicit configuration.

```bash
# Local run against a real project
GITLAB_URL=https://gitlab.example.com \
GITLAB_PROJECT_ID=123 \
GITLAB_TOKEN=glpat-xxxx \
RELEASE_TAG=v1.2.0 \
  uv run gitlab-release release

# Machine-readable output
uv run gitlab-release --json release

# Preview an SMTP notification (never actually sent yet)
uv run gitlab-release release --notify \
  --smtp-host smtp.example.com --smtp-from releases@example.com --smtp-to team@example.com
```

### `release` options

| Flag | Env var | Falls back to | Notes |
|---|---|---|---|
| `--gitlab-url` | `GITLAB_URL` | `CI_SERVER_URL` | |
| `--project-id` | `GITLAB_PROJECT_ID` | `CI_PROJECT_ID` | |
| `--token` | `GITLAB_TOKEN` | `CI_JOB_TOKEN` | Job token is read-only in this build |
| `--tag` | `RELEASE_TAG` | `CI_COMMIT_TAG` | |
| `--ca-bundle` | `REQUESTS_CA_BUNDLE` | — | For self-hosted instances |
| `--dry-run` / `--no-dry-run` | `RELEASE_DRY_RUN` | — | Default on; `--no-dry-run` is rejected (not implemented yet) |
| `--template` | `RELEASE_TEMPLATE` | — | Override the bundled changelog template |
| `--notify` / `--no-notify` | `RELEASE_NOTIFY` | — | Default off; previews, never sends |
| `--smtp-host` | `SMTP_HOST` | — | |
| `--smtp-port` | `SMTP_PORT` | — | Defaults to 25 |
| `--smtp-user` / `--smtp-password` | `SMTP_USER` / `SMTP_PASSWORD` | — | Omit both for an unauthenticated relay |
| `--smtp-from` / `--smtp-to` | `SMTP_FROM` / `SMTP_TO` | — | |
| `--smtp-starttls` / `--no-smtp-starttls` | `SMTP_STARTTLS` | — | Default off |

Top-level flags (`--verbose`/`-v`, `--quiet`/`-q`, `--json`) go before the subcommand:
`gitlab-release --verbose release ...`.

## Development

```bash
uv run pytest -q                        # tests
uv run ruff check --fix . && uv run ruff format .
uv run mypy src/
```

Project conventions, architecture, and the full list of what's still deferred are documented
in [CLAUDE.md](CLAUDE.md). Design and implementation history for each slice lives under
[docs/superpowers/](docs/superpowers/).
