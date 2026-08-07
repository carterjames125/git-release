# git-release

A Python CLI that runs inside a Docker container as a GitLab CI/CD job and automates the
release step of a pipeline: tagging, package upload, changelog generation, and email
notification. See [CLAUDE.md](CLAUDE.md) for the full target design.

**Current status:** the `release` command validates configuration, talks to the GitLab API to
build a real changelog from commits, contributors, and merge request approvers, and — with
`--no-dry-run` — creates the git tag and GitLab Release and uploads build artifacts to the
project's Generic Package Registry. `--dry-run` (the default) previews all of the above,
including an SMTP notification, without creating or uploading anything.

**Roadmap (not yet implemented):**
- Actually sending the SMTP notification — `--notify` only previews it today.
- Docker packaging — the multi-stage build described in [CLAUDE.md](CLAUDE.md) doesn't exist yet;
  run the CLI via `uv` (see [Running in GitLab CI](#running-in-gitlab-ci)) until it does.

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

# Full release: create the tag, upload every RPM under ./dist, and publish the GitLab Release
GITLAB_URL=https://gitlab.example.com \
GITLAB_PROJECT_ID=123 \
GITLAB_TOKEN=glpat-xxxx \
RELEASE_TAG=1.2.3-1 \
  uv run gitlab-release release --no-dry-run \
    --source-path ./dist --artifact-pattern '*.rpm'

# Group the changelog by GitLab MR label instead of Conventional Commit type, and treat an
# already-uploaded package file as success rather than failing the run
uv run gitlab-release release --no-dry-run \
  --source-path ./dist --changelog-group-by label --if-exists skip
```

### `release` options

| Flag | Env var | Falls back to | Notes |
|---|---|---|---|
| `--gitlab-url` | `GITLAB_URL` | `CI_SERVER_URL` | |
| `--project-id` | `GITLAB_PROJECT_ID` | `CI_PROJECT_ID` | |
| `--token` | `GITLAB_TOKEN` | `CI_JOB_TOKEN` | Job token can release and upload but cannot create tags |
| `--tag` | `RELEASE_TAG` | `CI_COMMIT_TAG` | |
| `--ca-bundle` | `REQUESTS_CA_BUNDLE` | — | For self-hosted instances |
| `--dry-run` / `--no-dry-run` | `RELEASE_DRY_RUN` | — | Default on; `--no-dry-run` creates the tag, release, and uploads artifacts |
| `--template` | `RELEASE_TEMPLATE` | — | Override the bundled changelog template |
| `--changelog-group-by` | `RELEASE_CHANGELOG_GROUP_BY` | — | `type` / `label`, default `type`; groups changelog sections by Conventional Commit type or by the commit's GitLab MR labels (useful for teams without a commit-message convention) |
| `--source-path` | `RELEASE_SOURCE_PATH` | — | Directory of build artifacts to upload; omit to skip artifact upload |
| `--artifact-pattern` | `RELEASE_ARTIFACT_PATTERN` | — | Glob pattern for artifacts, relative to `--source-path`; defaults to `*` |
| `--package-name` | `RELEASE_PACKAGE_NAME` | — | Generic package name; defaults to the project's path slug |
| `--release-name` | `RELEASE_NAME` | — | GitLab release name; defaults to the tag |
| `--if-exists` | `RELEASE_IF_EXISTS` | — | `fail` / `skip` / `update`, default `fail`; behavior when the release or a package file already exists. Tag creation always fails if the tag already exists, regardless of this setting |
| `--notify` / `--no-notify` | `RELEASE_NOTIFY` | — | Default off; previews, never sends |
| `--smtp-host` | `SMTP_HOST` | — | |
| `--smtp-port` | `SMTP_PORT` | — | Defaults to 25 |
| `--smtp-user` / `--smtp-password` | `SMTP_USER` / `SMTP_PASSWORD` | — | Omit both for an unauthenticated relay |
| `--smtp-from` / `--smtp-to` | `SMTP_FROM` / `SMTP_TO` | — | |
| `--smtp-starttls` / `--no-smtp-starttls` | `SMTP_STARTTLS` | — | Default off |

Top-level flags (`--verbose`/`-v`, `--quiet`/`-q`, `--json`) go before the subcommand:
`gitlab-release --verbose release ...`.

**Note on `--template`:** the bundled template's `commits_by_type` context variable was renamed
to `commits_by_group` (loop variable `type` → `group_name`), and the Contributors/Approvers
sections are now passed as precomputed `contributors_table`/`approvers_table` markdown strings
instead of raw lists rendered via bullet-list loops. If you maintain a custom `--template` copied
from an earlier version of the bundled default, update it to match — a stale copy fails loudly
(`StrictUndefined` raises a template error, exit code 5) rather than silently rendering blank
sections.

## Running in GitLab CI

A job running inside GitLab CI needs almost no explicit configuration — `GITLAB_URL`,
`GITLAB_PROJECT_ID`, and the commit ref are picked up automatically from GitLab's predefined
`CI_SERVER_URL` / `CI_PROJECT_ID` / `CI_COMMIT_SHA` variables. You still need to supply a tag
(this tool consumes an already-decided tag — it does not compute the next version itself, see
`--tag` above) and a token capable of creating tags (`CI_JOB_TOKEN` cannot — a project access
token or personal access token with the `api` scope is required):

```yaml
release:
  stage: release
  image: python:3.12-slim  # a dedicated image isn't built yet - see Roadmap above
  rules:
    - changes:
        - scripts/**/*
        - ansible/**/*
  before_script:
    - pip install uv
    - uv sync --all-extras
  script:
    - uv run gitlab-release release --no-dry-run
      --tag "$NEXT_VERSION"
      --source-path ./dist
      --artifact-pattern '*.rpm'
  variables:
    GITLAB_TOKEN: $RELEASE_PROJECT_TOKEN
```

`NEXT_VERSION` stands in for whatever computes the next tag in your pipeline (a version-bump
script, a manually-set CI/CD variable, etc.). `RELEASE_PROJECT_TOKEN` should be a
[project access token](https://docs.gitlab.com/ee/user/project/settings/project_access_tokens.html)
or PAT with the `api` scope, stored as a masked/protected CI/CD variable — never `CI_JOB_TOKEN`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Unexpected/internal error |
| 2 | Usage or configuration error (missing settings, bad flag combination) |
| 3 | GitLab API error (auth, permissions, 5xx after retries exhausted) |
| 4 | Artifact error (source path empty/unreadable, package upload failed) |
| 5 | Template rendering error |

## Development

```bash
uv run pytest -q                        # tests
uv run ruff check --fix . && uv run ruff format .
uv run mypy src/
```
