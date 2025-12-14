import os
import requests

RESEND_API_KEY = os.getenv("RESEND_API_KEY")

def send_login_email(email: str, token: str):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY not set")

    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": "GreenCore <auth@greencore-api.ru>",
            "to": [email],
            "subject": "Код входа GreenCore",
            "html": f"""
            <div>
                <p>Ваш код входа:</p>
                <h2>{token}</h2>
                <p>Код действует 15 минут.</p>
            </div>
            """,
        },
        timeout=10,
    )

    if r.status_code >= 300:
        raise RuntimeError(f"Resend error: {r.text}")
