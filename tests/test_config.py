import pytest

from gitlab_release.config import load_settings
from gitlab_release.errors import ConfigError


def test_missing_everything_raises_single_aggregated_error() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_settings(
            gitlab_url=None,
            project_id=None,
            token=None,
            tag=None,
            ca_bundle=None,
            env={},
        )

    message = exc_info.value.message
    for field in ("gitlab_url", "project_id", "token", "tag", "ref"):
        assert field in message


def test_bare_ci_job_env_resolves_via_ci_defaults() -> None:
    env = {
        "CI_SERVER_URL": "https://gitlab.example.com",
        "CI_PROJECT_ID": "42",
        "CI_JOB_TOKEN": "job-token-value",
        "CI_COMMIT_TAG": "v1.2.3",
        "CI_COMMIT_SHA": "abc123",
    }

    settings = load_settings(
        gitlab_url=None,
        project_id=None,
        token=None,
        tag=None,
        ca_bundle=None,
        env=env,
    )

    assert settings.gitlab_url == "https://gitlab.example.com"
    assert settings.project_id == "42"
    assert settings.token == "job-token-value"
    assert settings.is_job_token is True
    assert settings.tag == "v1.2.3"
    assert settings.ref == "abc123"


def test_explicit_token_wins_over_job_token() -> None:
    env = {
        "CI_SERVER_URL": "https://gitlab.example.com",
        "CI_PROJECT_ID": "42",
        "CI_JOB_TOKEN": "job-token-value",
        "CI_COMMIT_TAG": "v1.2.3",
        "CI_COMMIT_SHA": "abc123",
    }

    settings = load_settings(
        gitlab_url=None,
        project_id=None,
        token="explicit-token",
        tag=None,
        ca_bundle=None,
        env=env,
    )

    assert settings.token == "explicit-token"
    assert settings.is_job_token is False


def test_explicit_param_wins_over_differing_ci_default() -> None:
    env = {
        "CI_SERVER_URL": "https://ci-default.example.com",
        "CI_PROJECT_ID": "42",
        "CI_JOB_TOKEN": "job-token-value",
        "CI_COMMIT_TAG": "v1.2.3",
        "CI_COMMIT_SHA": "abc123",
    }

    settings = load_settings(
        gitlab_url="https://explicit.example.com",
        project_id=None,
        token=None,
        tag=None,
        ca_bundle=None,
        env=env,
    )

    assert settings.gitlab_url == "https://explicit.example.com"


def test_repr_masks_token_but_shows_other_fields() -> None:
    env = {
        "CI_SERVER_URL": "https://gitlab.example.com",
        "CI_PROJECT_ID": "42",
        "CI_JOB_TOKEN": "super-secret-token",
        "CI_COMMIT_TAG": "v1.2.3",
        "CI_COMMIT_SHA": "abc123",
    }
    settings = load_settings(
        gitlab_url=None,
        project_id=None,
        token=None,
        tag=None,
        ca_bundle=None,
        env=env,
    )

    text = repr(settings)
    assert "super-secret-token" not in text
    assert "***MASKED***" in text
    assert "v1.2.3" in text
    assert "https://gitlab.example.com" in text


def test_ca_bundle_optional() -> None:
    env = {
        "CI_SERVER_URL": "https://gitlab.example.com",
        "CI_PROJECT_ID": "42",
        "CI_JOB_TOKEN": "job-token-value",
        "CI_COMMIT_TAG": "v1.2.3",
        "CI_COMMIT_SHA": "abc123",
    }
    settings = load_settings(
        gitlab_url=None,
        project_id=None,
        token=None,
        tag=None,
        ca_bundle=None,
        env=env,
    )

    assert settings.ca_bundle is None


def test_missing_only_tag_names_only_tag() -> None:
    env = {
        "CI_SERVER_URL": "https://gitlab.example.com",
        "CI_PROJECT_ID": "42",
        "CI_JOB_TOKEN": "job-token-value",
        "CI_COMMIT_SHA": "abc123",
    }

    with pytest.raises(ConfigError) as exc_info:
        load_settings(
            gitlab_url=None,
            project_id=None,
            token=None,
            tag=None,
            ca_bundle=None,
            env=env,
        )

    message = exc_info.value.message
    assert "tag" in message
    for field in ("gitlab_url", "project_id", "token", "ref"):
        assert field not in message


from gitlab_release.config import NotifySettings, load_notify_settings


def test_load_notify_settings_success() -> None:
    settings = load_notify_settings(
        smtp_host="smtp.example.com", smtp_port=587, smtp_user=None, smtp_password=None,
        smtp_from="releases@example.com", smtp_to="team@example.com", smtp_starttls=False,
    )
    assert settings.smtp_host == "smtp.example.com"
    assert settings.smtp_port == 587
    assert settings.smtp_starttls is False


def test_load_notify_settings_defaults_port_to_25() -> None:
    settings = load_notify_settings(
        smtp_host="smtp.example.com", smtp_port=None, smtp_user=None, smtp_password=None,
        smtp_from="releases@example.com", smtp_to="team@example.com", smtp_starttls=False,
    )
    assert settings.smtp_port == 25


def test_load_notify_settings_missing_fields_raises_aggregated_config_error() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_notify_settings(
            smtp_host=None, smtp_port=None, smtp_user=None, smtp_password=None,
            smtp_from=None, smtp_to="team@example.com", smtp_starttls=False,
        )
    message = exc_info.value.message
    assert "smtp_host" in message
    assert "smtp_from" in message
    assert "smtp_to" not in message


def test_notify_settings_repr_masks_password() -> None:
    settings = load_notify_settings(
        smtp_host="smtp.example.com", smtp_port=587, smtp_user="u", smtp_password="s3cr3t",
        smtp_from="releases@example.com", smtp_to="team@example.com", smtp_starttls=False,
    )
    text = repr(settings)
    assert "s3cr3t" not in text
    assert "***MASKED***" in text
    assert "smtp.example.com" in text
