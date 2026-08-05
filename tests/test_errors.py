import pytest

from gitlab_release.errors import (
    ArtifactError,
    ConfigError,
    GitLabAPIError,
    InternalError,
    ReleaseError,
    TemplateError,
)


@pytest.mark.parametrize(
    ("exc_cls", "expected_code"),
    [
        (InternalError, 1),
        (ConfigError, 2),
        (GitLabAPIError, 3),
        (ArtifactError, 4),
        (TemplateError, 5),
    ],
)
def test_exit_code_matches_table(exc_cls: type[ReleaseError], expected_code: int) -> None:
    assert exc_cls("boom").exit_code == expected_code


def test_message_round_trips() -> None:
    exc = ReleaseError("something broke")
    assert exc.message == "something broke"
    assert str(exc) == "something broke"


@pytest.mark.parametrize(
    "exc_cls", [InternalError, ConfigError, GitLabAPIError, ArtifactError, TemplateError]
)
def test_subclasses_are_release_error(exc_cls: type[ReleaseError]) -> None:
    assert isinstance(exc_cls("boom"), ReleaseError)
