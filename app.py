from fastapi import FastAPI, Header, HTTPException, Query, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os
from typing import Optional, Literal
from datetime import datetime, timedelta
import secrets
from fastapi.responses import JSONResponse
from utils.notify import send_alert
import uuid
import requests

# ────────────────────────────────
# ⚙️ Конфигурация
# ────────────────────────────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
MASTER_KEY = os.getenv("MASTER_KEY")
YK_SHOP_ID = os.getenv("YK_SHOP_ID")
YK_SECRET_KEY = os.getenv("YK_SECRET_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://www.greencore-api.ru")

app = FastAPI()
engine = create_engine(DATABASE_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://web-production-93a9e.up.railway.app",
        "https://web-production-310c7c.up.railway.app",
        "https://greencore-api.ru",
        "https://www.greencore-api.ru"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LIGHT_PATTERNS = {
    "тень": ["full shade", "shade", "тень", "indirect", "diffused"],
    "полутень": ["part shade", "partial", "полутень", "рассеян", "утреннее"],
    "яркий": ["full sun", "sun", "прямое солнце", "яркий", "солнеч"],
}

# ────────────────────────────────
# 🧠 Middleware проверки API-ключа
# ────────────────────────────────
@app.middleware("http")
async def verify_dynamic_api_key(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    open_paths = [
        "/docs",
        "/openapi.json",
        "/health",
        "/plans",
        "/create_user_key",
        "/generate_key",
        "/api/payment/session",
        "/api/payment/webhook",
        "/api/payments/latest",
    ]

    if any(request.url.path.rstrip("/").startswith(p.rstrip("/")) for p in open_paths):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT active, expires_at, requests, limit_total, max_page
                FROM api_keys
                WHERE api_key = :key
            """),
            {"key": api_key},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not row["active"]:
        raise HTTPException(status_code=403, detail="Inactive API key")

    if row["expires_at"] and row["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=403, detail="API key expired")

    if row["limit_total"] is not None and row["requests"] >= row["limit_total"]:
        raise HTTPException(status_code=429, detail="Request limit exceeded")

    request.state.max_page = row["max_page"]

    response = await call_next(request)

    if response.status_code < 400:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE api_keys SET requests = requests + 1 WHERE api_key = :key"),
                {"key": api_key},
            )

    return response

# ────────────────────────────────
# 🌿 /plants
# ────────────────────────────────
@app.get("/plants")
def get_plants(
    request: Request,
    view: Optional[str] = Query(None),
    light: Optional[Literal["тень", "полутень", "яркий"]] = Query(None),
    toxicity: Optional[Literal["none", "mild", "toxic"]] = Query(None),
    placement: Optional[Literal["комнатное", "садовое"]] = Query(None),
    sort: Optional[Literal["id", "random"]] = Query("random"),
    limit: Optional[int] = Query(None, ge=1, le=100),
):
    plan_cap = getattr(request.state, "max_page", None)
    applied_limit = min(limit or 50, plan_cap) if plan_cap else (limit or 50)

    query = "SELECT * FROM plants WHERE 1=1"
    params = {}

    if view:
        query += " AND LOWER(view) LIKE :v"
        params["v"] = f"%{view.lower()}%"

    if toxicity:
        query += " AND toxicity = :t"
        params["t"] = toxicity

    if placement == "комнатное":
        query += " AND indoor = true"
    elif placement == "садовое":
        query += " AND outdoor = true"

    query += " ORDER BY RANDOM()" if sort == "random" else " ORDER BY id"
    query += " LIMIT :limit"
    params["limit"] = applied_limit

    with engine.connect() as conn:
        rows = conn.execute(text(query), params)
        return {"count": rows.rowcount, "results": [dict(r._mapping) for r in rows]}

# ────────────────────────────────
# ❤️ health
# ────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}

# ────────────────────────────────
# 📦 планы
# ────────────────────────────────
@app.get("/plans")
def get_plans():
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                id,
                name,
                price_rub AS price,
                COALESCE(limit_total, 0) AS limit_total,
                COALESCE(max_page, 50) AS max_page
            FROM plans
            ORDER BY id ASC
        """))
        plans = [dict(r._mapping) for r in rows]
    return {"count": len(plans), "plans": plans}

# ────────────────────────────────
# 🆓 FREE / PAID — создание ключа по EMAIL
# ────────────────────────────────
@app.post("/create_user_key")
def create_user_key(email: str, plan: str = "free"):
    email = email.strip().lower()

    if plan == "free":
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT created_at FROM api_keys
                    WHERE plan_name='free' AND owner_email=:e
                    ORDER BY created_at DESC
                    LIMIT 1
                """),
                {"e": email},
            ).fetchone()

        if row and (datetime.utcnow() - row._mapping["created_at"]) < timedelta(hours=24):
            raise HTTPException(status_code=429, detail="Free key only once per 24h")

    return generate_api_key(MASTER_KEY, owner=email, plan=plan)

# ────────────────────────────────
# 🔐 ADMIN генерация ключа
# ────────────────────────────────
@app.post("/generate_key")
def generate_api_key(
    x_api_key: str = Header(...),
    owner: str = "user",
    plan: str = "free",
):
    if x_api_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Admin key required")

    owner = owner.strip().lower()
    now = datetime.utcnow()
    expires = now + timedelta(days=90) if plan == "free" else None

    with engine.begin() as conn:
        key = secrets.token_hex(32)

        limits = conn.execute(
            text("SELECT limit_total, max_page FROM plans WHERE LOWER(name)=LOWER(:p)"),
            {"p": plan},
        ).fetchone()

        conn.execute(
            text("""
                INSERT INTO api_keys
                (api_key, owner, owner_email, plan_name, active, expires_at, limit_total, max_page, source)
                VALUES
                (:k, :o, :e, :p, TRUE, :ex, :lt, :mp, :src)
            """),
            {
                "k": key,
                "o": owner,
                "e": owner,
                "p": plan,
                "ex": expires,
                "lt": limits.limit_total if limits else None,
                "mp": limits.max_page if limits else None,
                "src": "free" if plan == "free" else "payment",
            },
        )

    return {"api_key": key, "plan": plan}

# ────────────────────────────────
# 💳 YooKassa session
# ────────────────────────────────
@app.post("/api/payment/session")
def create_payment_session(email: str, plan: str):
    email = email.lower()

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT price_rub FROM plans WHERE LOWER(name)=LOWER(:p)"),
            {"p": plan},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Plan not found")

    payment_body = {
        "amount": {"value": f"{row.price_rub:.2f}", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": f"{FRONTEND_URL}/payment/success"},
        "capture": True,
        "description": f"GreenCore {plan}",
        "receipt": {"customer": {"email": email}},
    }

    r = requests.post(
        "https://api.yookassa.ru/v3/payments",
        auth=(YK_SHOP_ID, YK_SECRET_KEY),
        json=payment_body,
        headers={"Idempotence-Key": str(uuid.uuid4())},
    )

    payment = r.json()

    if "confirmation" not in payment:
      raise HTTPException(
          status_code=400,
          detail=payment.get("description", "Payment error"),
      )

    return {
        "payment_url": payment["confirmation"]["confirmation_url"]
}


# ────────────────────────────────
# 💬 YooKassa webhook (идемпотентный)
# ────────────────────────────────
@app.post("/api/payment/webhook")
async def yookassa_webhook(request: Request):
    payload = await request.json()
    payment = payload.get("object", {})
    payment_id = payment.get("id")
    status = payment.get("status")

    if not payment_id:
        return {"ignored": True}

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT plan_name, email, api_key
                FROM pending_payments
                WHERE payment_id=:pid
                FOR UPDATE
            """),
            {"pid": payment_id},
        ).mappings().first()

        if not row:
            return {"ignored": True}

        if status == "succeeded" and row["api_key"] is None:
            key = secrets.token_hex(32)

            limits = conn.execute(
                text("SELECT limit_total, max_page FROM plans WHERE LOWER(name)=LOWER(:p)"),
                {"p": row["plan_name"]},
            ).fetchone()

            conn.execute(
                text("""
                    INSERT INTO api_keys
                    (api_key, owner, owner_email, plan_name, active, limit_total, max_page, source)
                    VALUES (:k, :o, :e, :p, TRUE, :lt, :mp, 'payment')
                """),
                {
                    "k": key,
                    "o": row["email"],
                    "e": row["email"],
                    "p": row["plan_name"],
                    "lt": limits.limit_total if limits else None,
                    "mp": limits.max_page if limits else None,
                },
            )

            conn.execute(
                text("""
                    UPDATE pending_payments
                    SET api_key=:k, status='succeeded', paid_at=NOW()
                    WHERE payment_id=:pid
                """),
                {"k": key, "pid": payment_id},
            )

    return {"received": True}
