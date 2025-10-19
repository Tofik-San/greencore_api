from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os
from typing import Optional, Literal
from fastapi.openapi.utils import get_openapi
from datetime import datetime, timedelta
import secrets

# ────────────────────────────────
# 🔧 Загрузка конфигурации
# ────────────────────────────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
MASTER_KEY = os.getenv("MASTER_KEY")

app = FastAPI()

# ────────────────────────────────
# 🌐 CORS
# ────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(DATABASE_URL)

# ────────────────────────────────
# 💡 Паттерны освещённости
# ────────────────────────────────
LIGHT_PATTERNS = {
    "тень": ["full shade", "shade", "тень", "indirect", "diffused"],
    "полутень": ["part shade", "partial", "полутень", "рассеян", "утреннее"],
    "яркий": ["full sun", "sun", "прямое солнце", "яркий", "солнеч"],
}

COOLDOWN_DAYS = {"free": 1, "premium": 0, "supreme": 0}

# ────────────────────────────────
# 🌿 /plants
# ────────────────────────────────
@app.get("/plants")
def get_plants(
    view: Optional[str] = Query(None, description="Название вида или сорта"),
    light: Optional[Literal["тень", "полутень", "яркий"]] = Query(None, description="Освещённость"),
    zone_usda: Optional[Literal["2","3","4","5","6","7","8","9","10","11","12"]] = Query(None, description="Климатическая зона USDA"),
    toxicity: Optional[Literal["none","mild","toxic"]] = Query(None, description="Таксичность растения"),
    placement: Optional[Literal["комнатное","садовое"]] = Query(None, description="Тип размещения"),
    sort: Optional[Literal["id","random"]] = Query("random", description="Порядок сортировки"),
    limit: int = Query(50, ge=1, le=100, description="Количество карточек")
):
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

    # устойчивый к тире фильтр USDA
    if zone_usda:
        z_input = zone_usda.strip()
        try:
            z = int(z_input)
            query += """
                AND (
                    TRIM(COALESCE(filter_zone_usda, '')) != ''
                    AND (
                        (CASE WHEN POSITION('-' IN REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-'))>0
                              THEN SPLIT_PART(REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-'),'-',1)
                              ELSE REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-') END)::int <= :z
                        AND
                        (CASE WHEN POSITION('-' IN REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-'))>0
                              THEN SPLIT_PART(REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-'),'-',2)
                              ELSE REPLACE(REPLACE(filter_zone_usda,'–','-'),'—','-') END)::int >= :z
                    )
                )
            """
            params["z"] = z
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
    params["limit"] = limit

    with engine.connect() as connection:
        result = connection.execute(text(query), params)
        plants = [dict(row._mapping) for row in result]
    return {"count": len(plants), "limit": limit, "results": plants}

# ────────────────────────────────
# 🔍 /plant/{id}
# ────────────────────────────────
@app.get("/plant/{plant_id}")
def get_plant(plant_id: int):
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM plants WHERE id = :id"), {"id": plant_id}
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Plant not found")
    return dict(row._mapping)

# ────────────────────────────────
# 📊 /stats
# ────────────────────────────────
@app.get("/stats")
def get_stats():
    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT 
                COUNT(*) AS total,
                COUNT(DISTINCT view) AS unique_views,
                COUNT(DISTINCT family) AS unique_families,
                SUM(CASE WHEN toxicity='toxic' THEN 1 ELSE 0 END) AS toxic_count,
                SUM(CASE WHEN beginner_friendly=true THEN 1 ELSE 0 END) AS beginner_friendly_count
            FROM plants;
        """)).fetchone()
    return dict(row._mapping)

@app.get("/health")
def health_check():
    return {"status": "ok"}

# ────────────────────────────────
# 🗝️ Генерация API-ключей
# ────────────────────────────────
@app.post("/generate_key")
def generate_api_key(x_api_key: str = Header(...), owner: Optional[str] = "user", plan: str = "free"):
    if x_api_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Access denied: admin key required")

    owner_norm = owner.strip().lower()
    now = datetime.utcnow()

    with engine.begin() as conn:
        active_row = conn.execute(
            text("SELECT id FROM api_keys WHERE LOWER(owner)=:o AND active=TRUE"),
            {"o": owner_norm}
        ).fetchone()
        if active_row:
            raise HTTPException(status_code=403, detail="Active API key already exists for this owner")

        pending = conn.execute(
            text("SELECT next_issue_allowed FROM api_keys WHERE LOWER(owner)=:o ORDER BY created_at DESC LIMIT 1"),
            {"o": owner_norm}
        ).fetchone()
        if pending:
            pending_map = pending._mapping
            next_allowed = pending_map["next_issue_allowed"]
            if next_allowed and next_allowed > now:
                raise HTTPException(
                    status_code=403,
                    detail=f"New key not allowed until {next_allowed.isoformat()}"
                )

        new_key = secrets.token_hex(32)
        expires = now + timedelta(days=90) if plan == "free" else None
        conn.execute(
            text("INSERT INTO api_keys (api_key, owner, plan_name, expires_at) VALUES (:k,:o,:p,:e)"),
            {"k": new_key, "o": owner_norm, "p": plan, "e": expires}
        )

    return {"api_key": new_key, "plan": plan, "expires_in_days": 90 if plan=="free" else None}

# ────────────────────────────────
# 🧠 Middleware проверки лимитов
# ────────────────────────────────
@app.middleware("http")
async def verify_dynamic_api_key(request: Request, call_next):
    open_paths = ["/docs", "/openapi.json", "/health", "/generate_key"]
    if any(request.url.path.startswith(p) for p in open_paths):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT k.active, k.expires_at, k.requests, k.plan_name, p.limit_total, p.max_page
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

    if "limit" in request.query_params and r["max_page"]:
        try:
            if int(request.query_params["limit"]) > r["max_page"]:
                raise HTTPException(status_code=400, detail=f"Max 'limit' for your plan is {r['max_page']}")
        except ValueError:
            pass

    response = await call_next(request)

    now = datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(text("UPDATE api_keys SET requests=requests+1 WHERE api_key=:key"), {"key": api_key})
        r2 = conn.execute(text("""
            SELECT k.requests, k.plan_name, p.limit_total
            FROM api_keys k
            LEFT JOIN plans p ON LOWER(k.plan_name)=LOWER(p.name)
            WHERE k.api_key=:key
        """), {"key": api_key}).fetchone()

        if r2:
            m = r2._mapping
            if m["limit_total"] and m["requests"] >= m["limit_total"]:
                cooldown_days = COOLDOWN_DAYS.get(m["plan_name"], 0)
                next_allowed = now + timedelta(days=cooldown_days) if cooldown_days > 0 else None
                if next_allowed:
                    conn.execute(text("""
                        UPDATE api_keys
                        SET active=FALSE, next_issue_allowed=:next_allowed
                        WHERE api_key=:key
                    """), {"next_allowed": next_allowed, "key": api_key})
                else:
                    conn.execute(text("UPDATE api_keys SET active=FALSE WHERE api_key=:key"), {"key": api_key})
    return response

# ────────────────────────────────
# 📘 Swagger / OpenAPI
# ────────────────────────────────
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title="GreenCore API",
        version="2.3.0",
        description="GreenCore API — стабильная продакшн-версия с фильтрами USDA/токсичности, лимитами и защитой ключей.",
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["APIKeyHeader"] = {
        "type": "apiKey", "in": "header", "name": "X-API-Key"
    }
    for path in schema["paths"]:
        for method in schema["paths"][path]:
            schema["paths"][path][method]["security"] = [{"APIKeyHeader": []}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
