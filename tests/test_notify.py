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
