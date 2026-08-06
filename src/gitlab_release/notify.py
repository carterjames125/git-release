"""SMTP notification. Optional and non-fatal by definition."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from loguru import logger

from gitlab_release.config import NotifySettings


def send_notification(settings: NotifySettings, *, subject: str, markdown_body: str) -> None:
    """Never raises: a notification failure must not fail a job whose release already
    succeeded. Failures are logged as a warning and swallowed."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = settings.smtp_to
    message.set_content(markdown_body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except Exception as exc:  # noqa: BLE001 - notification failure must never fail the job
        logger.warning("Failed to send notification email: {}", exc)
