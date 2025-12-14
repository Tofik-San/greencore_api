from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from datetime import datetime, timedelta
import secrets

from database import engine
from .service import ttl_minutes, send_login_email

router = APIRouter(prefix="/auth", tags=["auth"])


# ====== MODELS ======

class LoginRequest(BaseModel):
    email: EmailStr


class VerifyToken(BaseModel):
    token: str


# ====== HELPERS ======

def generate_login_token() -> str:
    return secrets.token_urlsafe(32)


# ====== ROUTES ======

@router.post("/request-login")
def request_login(payload: LoginRequest):
    email = payload.email
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
                    "INSERT INTO users (email) VALUES (:email) "
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

    send_login_email(email, token)

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
                SELECT id, user_id, expires_at, used
                FROM auth_tokens
                WHERE token = :token
                """
            ),
            {"token": token},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=400, detail="Invalid token")

        if row["used"]:
            raise HTTPException(status_code=400, detail="Token already used")

        if row["expires_at"] < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Token expired")

        conn.execute(
            text(
                "UPDATE auth_tokens SET used = true WHERE id = :id"
            ),
            {"id": row["id"]},
        )

    return {
        "status": "ok",
        "user_id": row["user_id"],
    }
