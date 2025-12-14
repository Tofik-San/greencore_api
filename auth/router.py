from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from datetime import datetime
import secrets
import os

from database import engine
from .service import ttl_minutes, send_login_email

router = APIRouter(prefix="/auth", tags=["auth"])


# ===== MODELS =====

class LoginRequest(BaseModel):
    email: EmailStr


class VerifyToken(BaseModel):
    token: str


# ===== HELPERS =====

def generate_login_token() -> str:
    return secrets.token_urlsafe(32)


def assert_smtp_env():
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"SMTP env missing: {', '.join(missing)}")


# ===== ROUTES =====

@router.post("/request-login")
def request_login(payload: LoginRequest):
    email = payload.email.lower()
    token = generate_login_token()
    expires_at = ttl_minutes(15)

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email},
        ).mappings().first()

        if not row:
            row = conn.execute(
                text(
                    "INSERT INTO users (email, plan_name) "
                    "VALUES (:email, 'free') "
                    "RETURNING id"
                ),
                {"email": email},
            ).mappings().first()

        user_id = row["id"]

        conn.execute(
            text(
                """
                INSERT INTO auth_tokens (user_id, token, expires_at, used)
                VALUES (:uid, :token, :exp, false)
                """
            ),
            {
                "uid": user_id,
                "token": token,
                "exp": expires_at,
            },
        )

    # 🔴 КРИТИЧЕСКОЕ МЕСТО
    try:
        assert_smtp_env()
        send_login_email(email, token)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Email send failed: {str(e)}"
        )

    return {
        "status": "ok",
        "email": email,
        "expires_in_sec": 15 * 60,
    }


@router.post("/verify")
def verify_login_token(payload: VerifyToken):
    token = payload.token

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT t.id AS token_id, t.user_id, t.expires_at, t.used, u.api_key
                FROM auth_tokens t
                JOIN users u ON u.id = t.user_id
                WHERE t.token = :token
                """
            ),
            {"token": token},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=400, detail="invalid_token")

        if row["used"]:
            raise HTTPException(status_code=400, detail="token_already_used")

        if row["expires_at"] < datetime.utcnow():
            raise HTTPException(status_code=400, detail="token_expired")

        conn.execute(
            text("UPDATE auth_tokens SET used = true WHERE id = :id"),
            {"id": row["token_id"]},
        )

        api_key = row["api_key"]
        if not api_key:
            api_key = secrets.token_hex(32)
            conn.execute(
                text("UPDATE users SET api_key = :k, last_login = now() WHERE id = :uid"),
                {"k": api_key, "uid": row["user_id"]},
            )
        else:
            conn.execute(
                text("UPDATE users SET last_login = now() WHERE id = :uid"),
                {"uid": row["user_id"]},
            )

    return {
        "status": "ok",
        "user_id": row["user_id"],
        "api_key": api_key,
    }
