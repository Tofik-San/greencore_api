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

# 🔔 уведомления
from utils.notify import send_alert

# ────────────────────────────────
# 🔧 Конфигурация
# ────────────────────────────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
MASTER_KEY = os.getenv("MASTER_KEY")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(DATABASE_URL)

LIGHT_PATTERNS = {
    "тень": ["full shade", "shade", "тень", "indirect", "diffused"],
    "полутень": ["part shade", "partial", "полутень", "рассеян", "утреннее"],
    "яркий": ["full sun", "sun", "прямое солнце", "яркий", "солнеч"],
}

COOLDOWN_DAYS = {"free": 1, "premium": 15, "supreme": 30}

# ────────────────────────────────
# 🌿 /plants
# ────────────────────────────────
@app.get("/plants")
def get_plants(
    view: Optional[str] = Query(None),
    light: Optional[Literal["тень", "полутень", "яркий"]] = Query(None),
    zone_usda: Optional[Literal["2","3","4","5","6","7","8","9","10","11","12"]] = Query(None),
    toxicity: Optional[Literal["none","mild","toxic"]] = Query(None),
    placement: Optional[Literal["комнатное","садовое"]] = Query(None),
    sort: Optional[Literal["id","random"]] = Query("random"),
    limit: int = Query(50, ge=1, le=100)
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
    params["limit"] = limit

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        plants = [dict(row._mapping) for row in result]
    return {"count": len(plants), "limit": limit, "results": plants}

# ────────────────────────────────
# 🧠 Middleware: алерты и ключи
# ────────────────────────────────
@app.middleware("http")
async def alert_5xx_middleware(request: Request, call_next):
    try:
        response = await call_next(request)
        if 500 <= response.status_code < 600:
            await send_alert(
                event_type="server_error",
                detail={"msg": "5xx response"},
                user_key=request.headers.get("X-API-Key"),
                endpoint=request.url.path,
                status_code=response.status_code,
            )
        return response
    except Exception as e:
        await send_alert(
            event_type="uncaught_exception",
            detail={"error": str(e)},
            user_key=request.headers.get("X-API-Key"),
            endpoint=request.url.path,
            status_code=500,
        )
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


@app.middleware("http")
async def verify_dynamic_api_key(request: Request, call_next):
    open_paths = [
        "/docs",
        "/openapi.json",
        "/health",
        "/generate_key",
        "/_alert_test",
        "/favicon.ico"
    ]
    if any(request.url.path.startswith(p) for p in open_paths):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        await send_alert("missing_api_key", {"msg": "Ключ не передан"}, None, request.url.path, 401)
        raise HTTPException(status_code=401, detail="Missing API key")

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT k.active, k.expires_at, k.requests, k.plan_name, p.limit_total, p.max_page
            FROM api_keys k
            LEFT JOIN plans p ON LOWER(k.plan_name)=LOWER(p.name)
            WHERE k.api_key=:key
        """), {"key": api_key}).fetchone()

    if not row:
        await send_alert("invalid_key", {"msg": "Неверный ключ"}, api_key, request.url.path, 403)
        raise HTTPException(status_code=403, detail="Invalid API key")

    r = row._mapping
    if not r["active"]:
        await send_alert("inactive_key", {"msg": "Ключ деактивирован"}, api_key, request.url.path, 403)
        raise HTTPException(status_code=403, detail="Inactive API key")

    if r["expires_at"] and r["expires_at"] < datetime.utcnow():
        await send_alert("expired_key", {"msg": "Срок действия ключа истёк"}, api_key, request.url.path, 403)
        raise HTTPException(status_code=403, detail="API key expired")

    if r["limit_total"] and r["requests"] >= r["limit_total"]:
        await send_alert("rate_limit_exceeded", {"msg": "Превышен лимит запросов"}, api_key, request.url.path, 429)
        raise HTTPException(status_code=429, detail="Request limit exceeded")

    if "limit" in request.query_params and r["max_page"]:
        try:
            if int(request.query_params["limit"]) > r["max_page"]:
                raise HTTPException(status_code=400, detail=f"Max 'limit' for your plan is {r['max_page']}")
        except ValueError:
            pass

    response = await call_next(request)

    with engine.begin() as conn:
        conn.execute(text("UPDATE api_keys SET requests=requests+1 WHERE api_key=:key"), {"key": api_key})

    return response

# ────────────────────────────────
# 🔔 Тестовый эндпоинт
# ────────────────────────────────
@app.get("/_alert_test")
async def _alert_test(request: Request):
    await send_alert("test", {"msg": "Система активна"}, request.headers.get("X-API-Key"), "/_alert_test", 200)
    return {"ok": True}

# ────────────────────────────────
# 📘 Swagger
# ────────────────────────────────
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title="GreenCore API",
        version="2.4.1",
        description="GreenCore API — уведомления и алерты Telegram, ключи, лимиты и фильтры USDA.",
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
