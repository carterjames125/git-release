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
