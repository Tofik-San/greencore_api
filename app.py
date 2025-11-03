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

# ────────────────────────────────
# ⚙️ Конфигурация
# ────────────────────────────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
MASTER_KEY = os.getenv("MASTER_KEY")

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
# 🧠 Middleware лимитов + логирование
# ────────────────────────────────
@app.middleware("http")
async def verify_dynamic_api_key(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    open_paths = ["/docs", "/openapi.json", "/health", "/generate_key",
                  "/create_user_key", "/_alert_test", "/favicon.ico", "/plans"]
    if any(request.url.path.rstrip("/").startswith(p.rstrip("/")) for p in open_paths):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT k.active, k.expires_at, k.requests, k.plan_name,
                   COALESCE(k.limit_total, p.limit_total) AS limit_total,
                   COALESCE(k.max_page, p.max_page) AS max_page
            FROM api_keys k
            LEFT JOIN plans p ON LOWER(k.plan_name)=LOWER(p.name)
            WHERE k.api_key=:key
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

    request.state.plan_name = r.get("plan_name")
    request.state.max_page = r.get("max_page")

    # выполняем запрос
    response = await call_next(request)

    # обновляем счётчик и логируем запрос
    with engine.begin() as conn:
        conn.execute(text("UPDATE api_keys SET requests=requests+1 WHERE api_key=:key"), {"key": api_key})
        conn.execute(
            text("INSERT INTO api_logs (api_key, endpoint, status_code) VALUES (:k, :e, :s)"),
            {"k": api_key, "e": request.url.path, "s": response.status_code}
        )

    return response

# ────────────────────────────────
# 🧩 /create_user_key — защита Free по IP
# ────────────────────────────────
@app.post("/create_user_key")
async def create_user_key(request: Request):
    try:
        data = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                data = await request.json()
            except Exception:
                data = {}
        plan = request.query_params.get("plan") or data.get("plan") or "free"
        plan = plan.strip().lower()
        ip = request.client.host
        print(f"[DEBUG] create_user_key received plan={plan}, ip={ip}")

        # ограничение на free ключи — 1 раз в 24 часа с одного IP
        if plan == "free":
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT created_at FROM api_keys WHERE plan_name='free' AND owner=:ip ORDER BY created_at DESC LIMIT 1"),
                    {"ip": ip}
                ).fetchone()
            if row and (datetime.utcnow() - row._mapping["created_at"]) < timedelta(hours=24):
                raise HTTPException(status_code=429, detail="Бесплатный ключ можно создавать не чаще одного раза в сутки.")

        result = generate_api_key(x_api_key=MASTER_KEY, owner=ip, plan=plan)
        return result

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] create_user_key failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ────────────────────────────────
# 🩺 Alert middleware
# ────────────────────────────────
@app.middleware("http")
async def alert_5xx_middleware(request: Request, call_next):
    try:
        response = await call_next(request)
        if 500 <= response.status_code < 600:
            await send_alert("server_error",
                             {"msg": "5xx response"},
                             request.headers.get("X-API-Key"),
                             request.url.path,
                             response.status_code)
        return response
    except Exception as e:
        await send_alert("uncaught_exception",
                         {"error": str(e)},
                         request.headers.get("X-API-Key"),
                         request.url.path,
                         500)
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

# ────────────────────────────────
# 📘 Swagger
# ────────────────────────────────
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title="GreenCore API",
        version="2.4.1",
        description="GreenCore API — корректные тарифы, лимиты и логирование запросов.",
        routes=app.routes,
    )
    schema["components"] = {"securitySchemes": {
        "APIKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    }}
    for path in schema["paths"]:
        for method in schema["paths"][path]:
            schema["paths"][path][method]["security"] = [{"APIKeyHeader": []}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
