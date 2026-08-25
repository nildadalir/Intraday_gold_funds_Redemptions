"""SMTP helper — settings from this project's config.yaml (via config.py)."""

from __future__ import annotations

import html
import os
import smtplib
import sys
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional, Sequence, Union

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402


def attach_directory_files(message, directory_path, *directory_files):
    if not directory_path:
        return
    if not os.path.exists(directory_path):
        raise FileNotFoundError(f"Directory not found: {directory_path}")
    for file in os.listdir(directory_path):
        if file not in directory_files:
            continue
        filepath = os.path.join(directory_path, file)
        if not os.path.isfile(filepath):
            continue
        with open(filepath, "rb") as attachment:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{file}"')
        message.attach(part)


def send_email(
    *,
    email_receiver: Union[str, Sequence[str]],
    email_subject: str,
    email_cc: Optional[Union[str, Sequence[str]]] = None,
    email_directory: Optional[Union[str, Path]] = None,
    email_files: Optional[Union[str, Sequence[str]]] = None,
    body: str,
) -> None:
    email_server = config.EMAIL_SMTP_SERVER
    email_port = config.EMAIL_SMTP_PORT
    email_sender = config.EMAIL_SENDER
    if not email_server or not email_sender:
        raise ValueError("email.smtp_server and email.sender must be set in config.yaml")

    required_values = {
        "email_receiver": email_receiver,
        "email_subject": email_subject,
        "body": body,
    }
    missing_values = [
        name
        for name, value in required_values.items()
        if value is None
        or (isinstance(value, str) and not value.strip())
        or (not isinstance(value, str) and not value)
    ]
    if missing_values:
        raise ValueError(
            "Required email input(s) cannot be null or empty: "
            + ", ".join(missing_values)
        )

    receivers = (
        [email_receiver] if isinstance(email_receiver, str) else list(email_receiver)
    )
    cc_list = [email_cc] if isinstance(email_cc, str) else list(email_cc or [])
    files = [email_files] if isinstance(email_files, str) else list(email_files or [])

    has_directory = bool(email_directory)
    has_files = bool(files)
    if has_directory != has_files:
        raise ValueError("email_directory and email_files must be provided together.")

    for receiver in receivers:
        message = MIMEMultipart()
        message["From"] = email_sender
        message["To"] = receiver
        message["Subject"] = email_subject
        if cc_list:
            message["Cc"] = ", ".join(cc_list)

        html_body = f"""
        <div dir="rtl" lang="fa"
             style="direction: rtl; text-align: right;
                    font-family: Tahoma, Arial, sans-serif;
                    font-size: 14pt; line-height: 1.8;
                    white-space: pre-line;">
            {html.escape(body)}
        </div>
        """
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(body, "plain", "utf-8"))
        alternative.attach(MIMEText(html_body, "html", "utf-8"))
        message.attach(alternative)
        if has_directory:
            attach_directory_files(message, email_directory, *files)

        attempts = config.EMAIL_SMTP_RETRIES
        delay = config.EMAIL_SMTP_RETRY_SECONDS
        timeout = config.EMAIL_SMTP_TIMEOUT
        all_recipients = [receiver] + cc_list
        payload = message.as_string()
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with smtplib.SMTP(email_server, email_port, timeout=timeout) as server:
                    server.sendmail(email_sender, all_recipients, payload)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(delay)
        if last_error is not None:
            raise last_error
