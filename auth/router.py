from fastapi import APIRouter, HTTPException
from .schemas import RequestLogin, VerifyToken
from .service import generate_login_token, ttl_minutes

# Локальный доступ к БД: без больших рефакторов, читаем тот же DATABASE_URL
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

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

    with engine.connect() as conn:
        # 1) находим или создаём пользователя
        row = conn.execute(
            text("SELECT id, api_key FROM users WHERE email = :email"),
            {"email": email},
        ).mappings().first()

        if not row:
            row = conn.execute(
                text("INSERT INTO users (email) VALUES (:email) RETURNING id, api_key"),
                {"email": email},
            ).mappings().first()

        user_id = row["id"]

        # 2) генерим одноразовый токен входа на 15 минут
        token = generate_login_token()
        expires_at = ttl_minutes(15)

        conn.execute(
            text("""
                INSERT INTO auth_tokens (user_id, token, expires_at, used)
                VALUES (:uid, :token, :exp, false)
            """),
            {"uid": user_id, "token": token, "exp": expires_at},
        )

    # На этом шаге почту НЕ шлём — возвращаем токен в ответе (для отладки/UI)
    return {
        "status": "ok",
        "email": email,
        "login_token": token,
        "expires_in_sec": 15 * 60,
    }

@router.post("/verify")
def verify_login_token(payload: VerifyToken):
    token = payload.token

    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT t.id AS token_id,
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

        # помечаем использованным
        conn.execute(
            text("UPDATE auth_tokens SET used = true WHERE id = :tid"),
            {"tid": row["token_id"]},
        )
        # фиксируем вход
        conn.execute(
            text("UPDATE users SET last_login = now() WHERE id = :uid"),
            {"uid": row["user_id"]},
        )

        # Возвращаем информацию пользователю:
        # на этом шаге — только текущий api_key (если уже существует) либо null.
        # Автогенерацию ключа сделаем на следующем шаге, чтобы не ломать твои планы/лимиты.
        return {
            "status": "ok",
            "user_id": row["user_id"],
            "api_key": row["api_key"],   # может быть null, это нормально
        }
