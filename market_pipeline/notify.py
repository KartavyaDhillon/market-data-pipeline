"""Delivery: email (SMTP) and Telegram. Each channel is skipped silently if not configured."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

import requests

log = logging.getLogger(__name__)


def send_email(subject: str, html: str, attachments: list[Path]) -> bool:
    host, user, pwd, to = (os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO"))
    if not all([host, user, pwd, to]):
        log.info("Email not configured - skipped")
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content("Please view this email in an HTML-capable client.")
    msg.add_alternative(html, subtype="html")
    for p in attachments:
        msg.add_attachment(p.read_bytes(), maintype="application", subtype="octet-stream", filename=p.name)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
    log.info("Email sent to %s", to)
    return True


def send_telegram(text: str, image: Path | None = None) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        log.info("Telegram not configured - skipped")
        return False
    base = f"https://api.telegram.org/bot{token}"
    if image:
        with image.open("rb") as f:
            r = requests.post(f"{base}/sendPhoto", data={"chat_id": chat, "caption": text[:1000]},
                              files={"photo": f}, timeout=30)
    else:
        r = requests.post(f"{base}/sendMessage", data={"chat_id": chat, "text": text}, timeout=30)
    r.raise_for_status()
    log.info("Telegram message sent")
    return True
