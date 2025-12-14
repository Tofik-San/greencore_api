from fastapi import FastAPI, Header, HTTPException, Query, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os
from typing import Optional, Literal
from datetime import datetime, timedelta
import secrets
from fastapi.responses import JSONResponse
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

# ────────────────────────────────
# 🧠 Middleware проверки ключа и лимитов (СТАРАЯ ЛОГИКА)
# ────────────────────────────────
@app.middleware("http")
async def verify_dynamic_api_key(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    open_paths = [
        "/docs", "/openapi.json", "/health",
        "/generate_key", "/create_user_key", "/plans",
        "/api/payment/session", "/api/payment/webhook", "/api/payments/latest"
    ]

    if any(request.url.path.rstrip("/").startswith(p.rstrip("/")) for p in open_paths):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT active, expires_at, requests,
                   COALESCE(limit_total, 0) AS limit_total,
                   COALESCE(max_page, 50) AS max_page
            FROM api_keys
            WHERE api_key=:key
        """), {"key": api_key}).fetchone()

    if not row:
        raise HTTPException(status_code=403, detail="Invalid API key")

    r = row._mapping
    if not r["active"]:
        raise HTTPException(status_code=403, detail="Inactive API key")
    if r["expires_at"] and r["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=403, detail="API key expired")
    if r["limit_total"] and r["requests"] >= r["limit_total"]:
        raise HTTPException(status_code=429, detail="Request limit exceeded")

    request.state.max_page = r["max_page"]

    response = await call_next(request)

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE api_keys SET requests=requests+1 WHERE api_key=:key"),
            {"key": api_key}
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
    limit: Optional[int] = Query(None, ge=1, le=100),
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
            SELECT id, name, price_rub AS price,
                   COALESCE(limit_total, 0) AS limit_total,
                   COALESCE(max_page, 50) AS max_page
            FROM plans
            ORDER BY id ASC
        """))
        plans = [dict(r._mapping) for r in rows]
    return {"count": len(plans), "plans": plans}

# ────────────────────────────────
# 🆓 FREE / PAID — создание ключа ПО EMAIL (ЕДИНСТВЕННАЯ ПРАВКА)
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

    return generate_api_key(
        x_api_key=MASTER_KEY,
        owner=email,
        owner_email=email,
        plan=plan
    )

# ────────────────────────────────
# 🔐 ADMIN генерация ключа (ЕДИНСТВЕННАЯ ПРАВКА: owner_email)
# ────────────────────────────────
@app.post("/generate_key")
def generate_api_key(
    x_api_key: str = Header(...),
    owner: str = "user",
    owner_email: Optional[str] = None,
    plan: str = "free",
):
    if x_api_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Admin key required")

    owner = owner.strip().lower()
    owner_email = owner_email.strip().lower() if owner_email else None
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
                (api_key, owner, owner_email, plan_name, active, expires_at, limit_total, max_page)
                VALUES
                (:k, :o, :e, :p, TRUE, :ex, :lt, :mp)
            """),
            {
                "k": key,
                "o": owner,
                "e": owner_email,
                "p": plan,
                "ex": expires,
                "lt": limits.limit_total if limits else None,
                "mp": limits.max_page if limits else None,
            },
        )

    return {"api_key": key, "plan": plan}
