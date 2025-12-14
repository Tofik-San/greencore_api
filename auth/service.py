import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
MAIL_FROM = os.getenv("MAIL_FROM")


def send_login_email(to_email: str, login_token: str):
    if not all([SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, MAIL_FROM]):
        raise RuntimeError("SMTP env vars not configured")

    msg = EmailMessage()
    msg["From"] = MAIL_FROM
    msg["To"] = to_email
    msg["Subject"] = "GreenCore — вход в аккаунт"

    msg.set_content(
        f"""
Здравствуйте!

Для входа в GreenCore используйте код:

{login_token}

Код действует 15 минут.
Если вы не запрашивали вход — просто проигнорируйте это письмо.
"""
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
