from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os
from typing import Optional, Literal
from fastapi.openapi.utils import get_openapi
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

app = FastAPI()
engine = create_engine(DATABASE_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://web-production-93a9e.up.railway.app",
        "https://web-production-310c7c.up.railway.app",
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
        if pats:
            clauses = []
            for i, pat in enumerate(pats):
                key = f"light_{i}"
                clauses.append(f"LOWER(light) LIKE :{key}")
                params[key] = f"%{pat.lower()}%"
            query += " AND (" + " OR ".join(clauses) + ")"

    if zone_usda:
        z_input = zone_usda.strip()
        try:
            z = int(z_input)
            zmin = max(z - 1, 1)
            zmax = min(z + 1, 12)
            query += """
                AND (
                    TRIM(COALESCE(filter_zone_usda, '')) != ''
                    AND (
                        (CASE WHEN POSITION('-' IN REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-'))>0
                              THEN SPLIT_PART(REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-'),'-',1)
                              ELSE REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-') END)::int <= :zmax
                        AND
                        (CASE WHEN POSITION('-' IN REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-'))>0
                              THEN SPLIT_PART(REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-'),'-',2)
                              ELSE REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-') END)::int >= :zmin
                    )
                )
            """
            params.update({"zmin": zmin, "zmax": zmax})
        except Exception:
            query += " AND COALESCE(filter_zone_usda,'') LIKE :zone"
            params["zone"] = f"%{z_input}%"

    if toxicity:
        query += " AND LOWER(toxicity) = :tox"
        params["tox"] = toxicity.lower()

    if placement:
        if placement == "комнатное":
            query += " AND indoor = true"
        elif placement == "садовое":
            query += " AND outdoor = true"

    query += " ORDER BY RANDOM()" if sort == "random" else " ORDER BY id"
    query += " LIMIT :limit"
    params["limit"] = applied_limit

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        plants = [dict(row._mapping) for row in result]
    return {"count": len(plants), "limit": applied_limit, "results": plants}

# ────────────────────────────────
# 💳 /api/payment/session — создание платежа
# ────────────────────────────────
@app.post("/api/payment/session")
def create_payment_session(request: Request):
    """Создание платежа в YooKassa и запись в pending_payments"""
    data = request.query_params or {}
    plan = data.get("plan", "free").lower()
    email = data.get("email", "unknown@example.com")

    if not YK_SHOP_ID or not YK_SECRET_KEY:
        raise HTTPException(status_code=500, detail="YooKassa credentials not set")

    # Получаем цену тарифа
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT price_rub FROM plans WHERE LOWER(name)=:p"), {"p": plan}
        ).fetchone()

        if not row or row.price_rub is None:
            raise HTTPException(status_code=404, detail="Plan not found or missing price")

        try:
            amount_value = float(str(row.price_rub).replace(",", "."))
        except Exception:
            amount_value = 0.0

    payment_body = {
        "amount": {"value": f"{amount_value:.2f}", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": "https://web-production-310c7c.up.railway.app/payment/success"
        },
        "capture": True,
        "description": f"GreenCore {plan.capitalize()} plan"
    }

    headers = {
        "Idempotence-Key": str(uuid.uuid4()),
        "Content-Type": "application/json"
    }

    r = requests.post(
        "https://api.yookassa.ru/v3/payments",
        auth=(YK_SHOP_ID, YK_SECRET_KEY),
        json=payment_body,
        headers=headers
    )

    if r.status_code not in (200, 201):
        print("[YooKassaError]", r.text)
        raise HTTPException(status_code=500, detail=f"YooKassa error: {r.text}")

    payment_data = r.json()
    payment_id = payment_data["id"]
    payment_url = payment_data["confirmation"]["confirmation_url"]

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO pending_payments (payment_id, plan_name, email, amount, status)
            VALUES (:pid, :plan, :email, :amount, 'pending')
        """), {"pid": payment_id, "plan": plan, "email": email, "amount": amount_value})

    return {"payment_id": payment_id, "payment_url": payment_url}
