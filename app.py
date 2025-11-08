from fastapi import FastAPI, Header, HTTPException, Query, Request, BackgroundTasks
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
    key_header = request.headers.get("X-API-Key")
    if not key_header:
        raise HTTPException(status_code=401, detail="Missing API key")

    with engine.connect() as conn:
        row = conn.execute(text("SELECT max_page FROM api_keys WHERE api_key=:k"), {"k": key_header}).fetchone()
        if row and row.max_page:
            request.state.max_page = row.max_page

    plan_cap = getattr(request.state, "max_page", None)
    user_limit = limit if limit is not None else 50
    if plan_cap and plan_cap < user_limit:
        applied_limit = plan_cap
    else:
        applied_limit = user_limit

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
# 🌿 Прочие эндпоинты
# ────────────────────────────────
@app.get("/plant/{plant_id}")
def get_plant(plant_id: int):
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM plants WHERE id=:id"), {"id": plant_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Plant not found")
    return dict(row._mapping)


@app.get("/plans")
def get_plans():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, name, price_rub AS price, limit_total, max_page FROM plans ORDER BY id"))
        plans = [dict(row._mapping) for row in result]
    return {"plans": plans, "count": len(plans)}


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ────────────────────────────────
# 🆓 Создание бесплатного API-ключа без оплаты
# ────────────────────────────────
@app.post("/create_user_key")
def create_user_key(plan: str = "free"):
    import secrets
    from datetime import datetime, timedelta

    new_key = secrets.token_hex(32)
    now = datetime.utcnow()
    expires = now + timedelta(days=90)

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO api_keys (api_key, owner, plan_name, expires_at, active, limit_total, max_page)
            VALUES (:k, 'guest', :p, :e, TRUE, 5, 5)
        """), {"k": new_key, "p": plan, "e": expires})

    return {"api_key": new_key, "plan": plan}


# ────────────────────────────────
# 🔐 Создание ключей
# ────────────────────────────────
@app.post("/generate_key")
def generate_api_key(x_api_key: str = Header(...), owner: Optional[str] = "user", plan: str = "free"):
    print(f"[DEBUG] generate_api_key called with plan={plan}, owner={owner}, key={x_api_key}")

    if x_api_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Access denied: admin key required")

    owner_norm = owner.strip().lower()
    now = datetime.utcnow()

    with engine.begin() as conn:
        conn.execute(text("UPDATE api_keys SET active=FALSE WHERE LOWER(owner)=:o AND active=TRUE"), {"o": owner_norm})
        new_key = secrets.token_hex(32)
        expires = now + timedelta(days=90) if plan == "free" else None

        plan_limits = conn.execute(
            text("SELECT limit_total, max_page FROM plans WHERE LOWER(name)=LOWER(:p)"),
            {"p": plan}
        ).fetchone()

        limit_total = plan_limits.limit_total if plan_limits else None
        max_page = plan_limits.max_page if plan_limits else None

        conn.execute(
            text("""
                INSERT INTO api_keys (api_key, owner, plan_name, expires_at, active, limit_total, max_page)
                VALUES (:k, :o, :p, :e, TRUE, :lt, :mp)
            """),
            {"k": new_key, "o": owner_norm, "p": plan, "e": expires, "lt": limit_total, "mp": max_page},
        )

    return {"api_key": new_key, "plan": plan, "limit_total": limit_total, "max_page": max_page}


# ────────────────────────────────
# 🔑 Получение последнего выданного ключа по email
# ────────────────────────────────
@app.get("/api/payments/latest")
def get_latest_payment(email: str):
    """Возвращает последний сгенерированный API-ключ для указанного email"""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT api_key
            FROM pending_payments
            WHERE email = :email AND api_key IS NOT NULL
            ORDER BY paid_at DESC
            LIMIT 1
        """), {"email": email}).fetchone()
    return {"api_key": row.api_key if row else None}


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

    with engine.connect() as conn:
        row = conn.execute(text("SELECT price_rub FROM plans WHERE LOWER(name)=:p"), {"p": plan}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Plan not found")
        amount_value = float(row.price_rub)

    payment_body = {
        "amount": {"value": f"{amount_value:.2f}", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": "https://web-production-93a9e.up.railway.app/payment/success",
        },
        "capture": True,
        "description": f"GreenCore {plan.capitalize()} plan",
    }

    headers = {"Idempotence-Key": str(uuid.uuid4()), "Content-Type": "application/json"}

    r = requests.post(
        "https://api.yookassa.ru/v3/payments",
        auth=(YK_SHOP_ID, YK_SECRET_KEY),
        json=payment_body,
        headers=headers,
    )

    if r.status_code not in (200, 201):
        print("[YooKassaError]", r.text)
        raise HTTPException(status_code=500, detail=f"YooKassa error: {r.text}")

    payment_data = r.json()
    payment_id = payment_data["id"]
    payment_url = payment_data["confirmation"]["confirmation_url"]

    with engine.begin() as conn:
        conn.execute(
            text(
                """
            INSERT INTO pending_payments (payment_id, plan_name, email, amount, status)
            VALUES (:pid, :plan, :email, :amount, 'pending')
        """
            ),
            {"pid": payment_id, "plan": plan, "email": email, "amount": amount_value},
        )

    return {"payment_id": payment_id, "payment_url": payment_url}


# ────────────────────────────────
# 💬 /api/payment/webhook — уведомления от YooKassa + автогенерация ключа
# ────────────────────────────────
@app.post("/api/payment/webhook")
async def yookassa_webhook(request: Request, background_tasks: BackgroundTasks):
    """Приём уведомлений от YooKassa, обновление статуса и генерация API-ключа"""
    try:
        payload = await request.json()
        event = payload.get("event")
        payment_obj = payload.get("object", {})
        payment_id = payment_obj.get("id")
        status = payment_obj.get("status")

        print(f"[YooKassaWebhook] event={event} status={status} id={payment_id}")

        if not payment_id:
            raise HTTPException(status_code=400, detail="Missing payment_id")

        def update_status_and_key():
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE pending_payments
                        SET status = :status, updated_at = NOW()
                        WHERE payment_id = :pid
                        """
                    ),
                    {"status": status, "pid": payment_id},
                )

                if status == "succeeded":
                    row = conn.execute(
                        text("SELECT plan_name, email FROM pending_payments WHERE payment_id=:pid"),
                        {"pid": payment_id},
                    ).fetchone()

                    if row:
                        plan, email = row.plan_name, row.email
                        new_key = secrets.token_hex(32)

                        plan_limits = conn.execute(
                            text("SELECT limit_total, max_page FROM plans WHERE LOWER(name)=LOWER(:p)"),
                            {"p": plan},
                        ).fetchone()

                        lt = plan_limits.limit_total if plan_limits else None
                        mp = plan_limits.max_page if plan_limits else None

                        conn.execute(
                            text(
                                """
                                INSERT INTO api_keys (api_key, owner, plan_name, active, limit_total, max_page)
                                VALUES (:k, :o, :p, TRUE, :lt, :mp)
                                """
                            ),
                            {"k": new_key, "o": email, "p": plan, "lt": lt, "mp": mp},
                        )

                        conn.execute(
                            text(
                                """
                                UPDATE pending_payments
                                SET api_key = :k, paid_at = NOW()
                                WHERE payment_id = :pid
                                """
                            ),
                            {"k": new_key, "pid": payment_id},
                        )

        background_tasks.add_task(update_status_and_key)

        if event == "payment.succeeded":
            background_tasks.add_task(
                send_alert,
                "payment_success",
                {"payment_id": payment_id, "status": status},
                None,
                "/api/payment/webhook",
                200,
            )

        return {"received": True}

    except Exception as e:
        print(f"[WebhookError] {e}")
        await send_alert("webhook_error", {"error": str(e)}, None, "/api/payment/webhook", 500)
        raise HTTPException(status_code=500, detail="Webhook error")
