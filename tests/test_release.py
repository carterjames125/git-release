import pytest
import responses

from gitlab_release.config import ArtifactSettings, Settings
from gitlab_release.errors import ArtifactError, ConfigError, GitLabAPIError
from gitlab_release.gitlab_client import GitlabClient
from gitlab_release.release import execute
from tests.gitlab_fixtures import (
    PROJECT_ID,
    register_empty_release,
    register_generic_package_upload,
    register_package_files_list,
    register_packages_list,
    register_release_create,
    register_release_get,
    register_tag_create,
    register_tag_get,
)


def _settings(*, is_job_token: bool = False, tag: str = "v1.2.3") -> Settings:
    return Settings(
        gitlab_url="https://gitlab.example.com",
        project_id=PROJECT_ID,
        token="t",
        is_job_token=is_job_token,
        tag=tag,
        ref="abc123",
        ca_bundle=None,
    )


def _client(*, is_job_token: bool = False) -> GitlabClient:
    return GitlabClient(
        url="https://gitlab.example.com",
        project_id=PROJECT_ID,
        token="t",
        is_job_token=is_job_token,
    )


@responses.activate
def test_job_token_with_no_dry_run_raises_config_error_before_any_mutation() -> None:
    register_empty_release(tag="v1.2.3")
    client = _client(is_job_token=True)
    settings = _settings(is_job_token=True)

    with pytest.raises(ConfigError):
        execute(
            client,
            settings,
            dry_run=False,
            artifact_settings=None,
            release_name="v1.2.3",
            if_exists="fail",
            template_path=None,
        )

    # Only the constructor's project GET happened - no tag/release calls at all.
    assert len(responses.calls) == 1


@responses.activate
def test_tag_already_exists_raises_gitlab_api_error() -> None:
    register_empty_release(tag="v1.2.3")
    register_tag_get("v1.2.3", exists=True)

    client = _client()
    settings = _settings()

    with pytest.raises(GitLabAPIError):
        execute(
            client,
            settings,
            dry_run=True,
            artifact_settings=None,
            release_name="v1.2.3",
            if_exists="fail",
            template_path=None,
        )


@responses.activate
def test_dry_run_full_flow_reports_without_mutating() -> None:
    register_empty_release(tag="v1.2.3")
    register_tag_get("v1.2.3", exists=False)
    register_release_get("v1.2.3", exists=False)

    client = _client()
    settings = _settings()

    result = execute(
        client,
        settings,
        dry_run=True,
        artifact_settings=None,
        release_name="v1.2.3",
        if_exists="fail",
        template_path=None,
    )

    assert result.tag_created is False
    assert result.release_url is None
    assert result.package_urls == []
    assert "v1.2.3" in result.changelog_text
    methods_called = {call.request.method for call in responses.calls}
    assert "POST" not in methods_called


@responses.activate
def test_no_dry_run_full_flow_creates_tag_uploads_artifact_creates_release(tmp_path) -> None:
    register_empty_release(tag="v1.2.3")
    register_tag_get("v1.2.3", exists=False)
    register_tag_create("v1.2.3")
    register_packages_list([])
    register_generic_package_upload("myapp", "1.2.3", "app.rpm")
    register_release_get("v1.2.3", exists=False)
    register_release_create(
        "v1.2.3", web_url="https://gitlab.example.com/group/project/-/releases/v1.2.3"
    )
    (tmp_path / "app.rpm").write_bytes(b"fake-rpm")

    client = _client()
    settings = _settings()
    artifact_settings = ArtifactSettings(
        source_path=tmp_path, pattern="*.rpm", package_name="myapp"
    )

    result = execute(
        client,
        settings,
        dry_run=False,
        artifact_settings=artifact_settings,
        release_name="v1.2.3",
        if_exists="fail",
        template_path=None,
    )

    assert result.tag_created is True
    assert result.release_url == "https://gitlab.example.com/group/project/-/releases/v1.2.3"
    assert result.package_urls == [
        {
            "name": "app.rpm",
            "url": (
                "https://gitlab.example.com/api/v4/projects/42/packages/generic/myapp/1.2.3/app.rpm"
            ),
        }
    ]


@responses.activate
def test_artifact_already_exists_if_exists_fail_raises_artifact_error(tmp_path) -> None:
    register_empty_release(tag="v1.2.3")
    register_tag_get("v1.2.3", exists=False)
    register_packages_list([{"id": 99, "name": "myapp", "version": "1.2.3"}])
    register_package_files_list(99, [{"file_name": "app.rpm"}])
    (tmp_path / "app.rpm").write_bytes(b"fake-rpm")

    client = _client()
    settings = _settings()
    artifact_settings = ArtifactSettings(
        source_path=tmp_path, pattern="*.rpm", package_name="myapp"
    )

    with pytest.raises(ArtifactError):
        execute(
            client,
            settings,
            dry_run=False,
            artifact_settings=artifact_settings,
            release_name="v1.2.3",
            if_exists="fail",
            template_path=None,
        )


@responses.activate
def test_artifact_already_exists_if_exists_skip_omits_it_from_package_urls(tmp_path) -> None:
    register_empty_release(tag="v1.2.3")
    register_tag_get("v1.2.3", exists=False)
    register_tag_create("v1.2.3")
    register_packages_list([{"id": 99, "name": "myapp", "version": "1.2.3"}])
    register_package_files_list(99, [{"file_name": "app.rpm"}])
    register_release_get("v1.2.3", exists=False)
    register_release_create("v1.2.3")
    (tmp_path / "app.rpm").write_bytes(b"fake-rpm")

    client = _client()
    settings = _settings()
    artifact_settings = ArtifactSettings(
        source_path=tmp_path, pattern="*.rpm", package_name="myapp"
    )

    result = execute(
        client,
        settings,
        dry_run=False,
        artifact_settings=artifact_settings,
        release_name="v1.2.3",
        if_exists="skip",
        template_path=None,
    )

    assert result.package_urls == []


@responses.activate
def test_release_already_exists_if_exists_fail_raises_gitlab_api_error() -> None:
    register_empty_release(tag="v1.2.3")
    register_tag_get("v1.2.3", exists=False)
    register_tag_create("v1.2.3")
    register_release_get("v1.2.3", exists=True)

    client = _client()
    settings = _settings()

    with pytest.raises(GitLabAPIError):
        execute(
            client,
            settings,
            dry_run=False,
            artifact_settings=None,
            release_name="v1.2.3",
            if_exists="fail",
            template_path=None,
        )


@responses.activate
def test_release_already_exists_if_exists_skip_returns_none_url() -> None:
    register_empty_release(tag="v1.2.3")
    register_tag_get("v1.2.3", exists=False)
    register_tag_create("v1.2.3")
    register_release_get("v1.2.3", exists=True)

    client = _client()
    settings = _settings()

    result = execute(
        client,
        settings,
        dry_run=False,
        artifact_settings=None,
        release_name="v1.2.3",
        if_exists="skip",
        template_path=None,
    )

    assert result.release_url is None


@responses.activate
def test_release_already_exists_if_exists_update_raises_config_error() -> None:
    register_empty_release(tag="v1.2.3")
    register_tag_get("v1.2.3", exists=False)
    register_tag_create("v1.2.3")
    register_release_get("v1.2.3", exists=True)

    client = _client()
    settings = _settings()

    with pytest.raises(ConfigError):
        execute(
            client,
            settings,
            dry_run=False,
            artifact_settings=None,
            release_name="v1.2.3",
            if_exists="update",
            template_path=None,
        )
