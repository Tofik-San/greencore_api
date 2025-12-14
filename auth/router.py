from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

from .schemas import RequestLogin, VerifyToken
from .service import generate_login_token, ttl_minutes, send_login_email

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/health")
def auth_health():
    return {"status": "auth module ready"}


@router.post("/request-login")
def request_login(payload: RequestLogin):
    email = payload.email.lower()

    with engine.begin() as conn:
        # 1. ищем пользователя
        row = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email},
        ).mappings().first()

        # 2. если нет — создаём
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

        # 3. генерируем токен
        token = generate_login_token()
        expires_at = ttl_minutes(15)

        # 4. сохраняем токен
        conn.execute(
            text(
                """
                INSERT INTO auth_tokens (user_id, token, expires_at, used)
                VALUES (:uid, :token, :exp, false)
                """
            ),
            {"uid": user_id, "token": token, "exp": expires_at},
        )

    # 5. ОТПРАВЛЯЕМ ПИСЬМО (вне транзакции)
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
                """
            ),
            {"token": token},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=400, detail="invalid_or_expired_token")

        # помечаем токен использованным
        conn.execute(
            text("UPDATE auth_tokens SET used = true WHERE id = :tid"),
            {"tid": row["token_id"]},
        )

        # фиксируем вход
        conn.execute(
            text("UPDATE users SET last_login = now() WHERE id = :uid"),
            {"uid": row["user_id"]},
        )

        api_key = row["api_key"]

        # первый вход — выдаём api_key
        if not api_key:
            api_key = generate_login_token()
            conn.execute(
                text("UPDATE users SET api_key = :k WHERE id = :uid"),
                {"k": api_key, "uid": row["user_id"]},
            )

        return {
            "status": "ok",
            "api_key": api_key,
        }
