from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from datetime import datetime, timedelta
import secrets

from database import engine
from .service import send_login_email

router = APIRouter(prefix="/auth", tags=["auth"])

# ===== MODELS =====

class LoginRequest(BaseModel):
    email: EmailStr


class VerifyToken(BaseModel):
    token: str


# ===== HELPERS =====

def generate_login_token() -> str:
    return secrets.token_urlsafe(32)


def ttl_minutes(minutes: int) -> datetime:
    return datetime.utcnow() + timedelta(minutes=minutes)


# ===== ROUTES =====

@router.get("/health")
def auth_health():
    return {"status": "auth module ready"}


@router.post("/request-login")
def request_login(payload: LoginRequest):
    email = payload.email.lower()
    token = generate_login_token()
    expires_at = ttl_minutes(15)

    with engine.begin() as conn:
        # user upsert
        user = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email},
        ).fetchone()

        if user:
            user_id = user.id
        else:
            user_id = conn.execute(
                text("""
                    INSERT INTO users (email, plan_name, created_at)
                    VALUES (:email, 'free', now())
                    RETURNING id
                """),
                {"email": email},
            ).scalar_one()

        conn.execute(
            text("""
                INSERT INTO auth_tokens (user_id, token, expires_at, used)
                VALUES (:uid, :token, :expires, false)
            """),
            {
                "uid": user_id,
                "token": token,
                "expires": expires_at,
            },
        )

    # отправка письма через Resend
    send_login_email(email, token)

    return {
        "status": "ok",
        "message": "login code sent",
        "expires_in_sec": 900,
    }


@router.post("/verify")
def verify_login_token(payload: VerifyToken):
    token = payload.token

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT
                    t.id AS token_id,
                    u.id AS user_id,
                    u.api_key
                FROM auth_tokens t
                JOIN users u ON u.id = t.user_id
                WHERE t.token = :token
                  AND t.used = false
                  AND t.expires_at > now()
                LIMIT 1
            """),
            {"token": token},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=400, detail="invalid_or_expired_token")

        # mark token used
        conn.execute(
            text("UPDATE auth_tokens SET used = true WHERE id = :tid"),
            {"tid": row["token_id"]},
        )

        # update last_login
        conn.execute(
            text("UPDATE users SET last_login = now() WHERE id = :uid"),
            {"uid": row["user_id"]},
        )

        api_key = row["api_key"]

        if not api_key:
            api_key = secrets.token_hex(32)
            conn.execute(
                text("UPDATE users SET api_key = :k WHERE id = :uid"),
                {"k": api_key, "uid": row["user_id"]},
            )

    return {
        "status": "ok",
        "user_id": row["user_id"],
        "api_key": api_key,
    }
