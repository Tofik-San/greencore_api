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
from auth.router import router as auth_router
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
app.include_router(auth_router)

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
# 🧠 Middleware проверки ключа и лимитов
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
        "/generate_key",
        "/create_user_key",
        "/api/payment/session",
        "/api/payment/webhook",
        "/api/payments/latest",
    ]

    if request.url.path.startswith("/auth"):
        return await call_next(request)

    if any(request.url.path.rstrip("/").startswith(p.rstrip("/")) for p in open_paths):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT active, expires_at, limit_total, requests, max_page
                FROM api_keys
                WHERE api_key = :key
                LIMIT 1
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
    zone_usda: Optional[Literal["2","3","4","5","6","7","8","9","10","11","12"]] = Query(None),
    toxicity: Optional[Literal["none","mild","toxic"]] = Query(None),
    placement: Optional[Literal["комнатное","садовое"]] = Query(None),
    category: Optional[str] = Query(None),
    sort: Optional[Literal["id","random"]] = Query("random"),
    limit: Optional[int] = Query(None, ge=1, le=100)
):
    plan_cap = getattr(request.state, "max_page", None)
    user_limit = limit if limit is not None else 50
    applied_limit = min(user_limit, plan_cap) if plan_cap else user_limit

    query = "SELECT * FROM plants WHERE 1=1"
    params = {}

    if view:
        query += " AND (LOWER(view) LIKE :view OR LOWER(cultivar) LIKE :view)"
        params["view"] = f"%{view.lower()}%"

    if light:
        pats = LIGHT_PATTERNS.get(light, [])
        clauses = []
        for i, pat in enumerate(pats):
            key = f"light_{i}"
            clauses.append(f"LOWER(light) LIKE :{key}")
            params[key] = f"%{pat.lower()}%"
        if clauses:
            query += " AND (" + " OR ".join(clauses) + ")"

    if placement == "комнатное":
        query += " AND indoor = true"
    elif placement == "садовое":
        query += " AND outdoor = true"

    if category:
        query += " AND LOWER(filter_category) = :cat"
        params["cat"] = category.lower()

    query += " ORDER BY RANDOM()" if sort == "random" else " ORDER BY id"
    query += " LIMIT :limit"
    params["limit"] = applied_limit

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        plants = [dict(row._mapping) for row in result]

    return {"count": len(plants), "limit": applied_limit, "results": plants}

# ────────────────────────────────
# 💬 /api/payment/webhook — ИДЕМПОТЕНТНЫЙ
# ────────────────────────────────
@app.post("/api/payment/webhook")
async def yookassa_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
        payment = payload.get("object", {})
        payment_id = payment.get("id")
        status = payment.get("status")

        if not payment_id:
            raise HTTPException(status_code=400, detail="Missing payment_id")

        def update_status_and_key():
            with engine.begin() as conn:
                row = conn.execute(
                    text("""
                        SELECT plan_name, email, api_key
                        FROM pending_payments
                        WHERE payment_id = :pid
                        FOR UPDATE
                    """),
                    {"pid": payment_id},
                ).mappings().first()

                if not row:
                    return

                conn.execute(
                    text("""
                        UPDATE pending_payments
                        SET status = :status, updated_at = NOW()
                        WHERE payment_id = :pid
                    """),
                    {"status": status, "pid": payment_id},
                )

                if status != "succeeded" or row["api_key"] is not None:
                    return

                plan, email = row["plan_name"], row["email"]
                new_key = secrets.token_hex(32)

                limits = conn.execute(
                    text("SELECT limit_total, max_page FROM plans WHERE LOWER(name)=LOWER(:p)"),
                    {"p": plan},
                ).fetchone()

                conn.execute(
                    text("""
                        INSERT INTO api_keys (api_key, owner, plan_name, active, limit_total, max_page)
                        VALUES (:k, :o, :p, TRUE, :lt, :mp)
                    """),
                    {
                        "k": new_key,
                        "o": email,
                        "p": plan,
                        "lt": limits.limit_total if limits else None,
                        "mp": limits.max_page if limits else None,
                    },
                )

                conn.execute(
                    text("""
                        UPDATE pending_payments
                        SET api_key = :k, paid_at = NOW()
                        WHERE payment_id = :pid
                    """),
                    {"k": new_key, "pid": payment_id},
                )

        background_tasks.add_task(update_status_and_key)
        return {"received": True}

    except Exception as e:
        await send_alert("webhook_error", {"error": str(e)}, None, "/api/payment/webhook", 500)
        raise HTTPException(status_code=500, detail="Webhook error")
