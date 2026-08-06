import pytest

_ENV_PREFIXES = ("GITLAB_", "CI_", "RELEASE_", "SMTP_")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """This repo's own CI is a GitLab job, so ambient CI_* vars are a real hazard
    for test hermeticity - strip everything relevant before each test."""
    import os

    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES) or key == "REQUESTS_CA_BUNDLE":
            monkeypatch.delenv(key, raising=False)
