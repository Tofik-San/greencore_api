from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os, json, secrets, logging
from datetime import datetime
from typing import Optional, Literal
from fastapi.openapi.utils import get_openapi
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.exception_handlers import request_validation_exception_handler

# ✅ Загрузка .env
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
MASTER_KEY = os.getenv("MASTER_KEY")

app = FastAPI(title="GreenCore API", version="1.7.2")

# 🌐 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(DATABASE_URL)

# 🌡 нормализация temperature
def norm_temp_sql(field="temperature"):
    return (
        "LOWER("
        f"REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({field}, '°', ''),'c',''),' ',''),'–','-'),'—','-')"
        ")"
    )

# 💡 Паттерны освещённости
LIGHT_PATTERNS = {
    "тень": ["full shade", "shade", "тень", "indirect", "diffused"],
    "полутень": ["part shade", "partial", "полутень", "рассеян", "утреннее"],
    "яркий": ["full sun", "sun", "прямое солнце", "яркий", "солнеч"],
}

# ✅ ------------------ ХЕЛПЕРЫ ------------------

def clamp_limit(request: Request, user_limit: int) -> int:
    max_page = getattr(request.state, "max_page", None)
    return min(user_limit, max_page) if max_page else user_limit

def filter_fields(items, allowed):
    allowed_set = set(allowed) if allowed else None
    if not allowed_set:
        return list(items)
    return [{k: v for k, v in it.items() if k in allowed_set} for it in items]

# ✅ ------------------ MIDDLEWARE ------------------

@app.middleware("http")
async def verify_key(request: Request, call_next):
    open_paths = ("/docs", "/openapi.json", "/health", "/generate_key")
    if any(request.url.path.startswith(p) for p in open_paths):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    row = engine.execute(text("""
        SELECT 
            k.id, k.active, k.requests, k.limit_total, k.max_page,
            p.name AS plan_name, p.allowed_filters, p.allowed_fields
        FROM api_keys k
        JOIN plans p ON p.id = k.plan_id
        WHERE k.api_key = :key
    """), {"key": api_key}).fetchone()

    if not row or not row.active:
        raise HTTPException(status_code=403, detail="Invalid or inactive key")

    if row.requests >= row.limit_total:
        raise HTTPException(status_code=402, detail="Request limit reached")

    def to_list(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except:
                return []
        return list(v) if v else []

    allowed_filters = to_list(row.allowed_filters)
    allowed_fields = to_list(row.allowed_fields)

    for q in request.query_params.keys():
        if q not in allowed_filters and q not in ("limit", "offset", "page", "search_field"):
            raise HTTPException(status_code=400, detail=f"Filter '{q}' not allowed for your plan")

    request.state.plan_name = row.plan_name
    request.state.allowed_filters = allowed_filters
    request.state.allowed_fields = allowed_fields
    request.state.max_page = row.max_page
    request.state.key_id = row.id

    response = await call_next(request)

    if response.status_code < 400:
        engine.execute(
            text("UPDATE api_keys SET requests = requests + 1 WHERE id = :id"),
            {"id": request.state.key_id}
        )

    return response

# ✅ ------------------ ЭНДПОИНТЫ ------------------

@app.get("/plants")
def get_plants(
    request: Request,
    search_field: Optional[Literal["view", "cultivar"]] = Query("view"),
    view: Optional[str] = Query(None),
    light: Optional[Literal["тень", "полутень", "яркий"]] = Query(None),
    temperature: Optional[str] = Query(None),
    toxicity: Optional[Literal["нет", "умеренно", "токсично"]] = Query(None),
    beginner_friendly: Optional[Literal["да", "нет"]] = Query(None),
    placement: Optional[Literal["комнатное", "садовое"]] = Query(None),
    limit: int = Query(20, ge=1, le=100)
):
    limit = clamp_limit(request, limit)

    query = "SELECT * FROM plants WHERE 1=1"
    params = {}

    if view:
        field = "view" if search_field == "view" else "cultivar"
        query += f" AND LOWER({field}) LIKE :val"
        params["val"] = f"%{view.lower()}%"

    if light:
        pats = LIGHT_PATTERNS.get(light, [])
        if pats:
            clauses = []
            for i, pat in enumerate(pats):
                key = f"light_{i}"
                clauses.append(f"LOWER(light) LIKE :{key}")
                params[key] = f"%{pat.lower()}%"
            query += " AND (" + " OR ".join(clauses) + ")"

    if temperature:
        t = temperature.lower().replace("°", "").replace("c", "").replace(" ", "").replace("–", "-").replace("—", "-")
        query += f" AND {norm_temp_sql('temperature')} LIKE :temp"
        params["temp"] = f"%{t}%"

    if toxicity:
        tox_map = {"нет": "none", "умеренно": "mild", "токсично": "toxic"}
        query += " AND LOWER(toxicity) = :tox"
        params["tox"] = tox_map[toxicity]

    if beginner_friendly:
        query += " AND beginner_friendly = :bf"
        params["bf"] = (beginner_friendly == "да")

    if placement:
        if placement == "комнатное":
            query += " AND indoor = true"
        elif placement == "садовое":
            query += " AND outdoor = true"

    query += " ORDER BY id LIMIT :limit"
    params["limit"] = limit

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        plants = [dict(row._mapping) for row in result]

    plants = filter_fields(plants, request.state.allowed_fields)
    return {"count": len(plants), "limit": limit, "results": plants}


@app.post("/generate_key")
def generate_api_key(x_api_key: str = Header(...), plan: str = "free", owner: str = "user"):
    if x_api_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Access denied: admin key required")

    new_key = secrets.token_hex(32)
    with engine.begin() as conn:
        plan_row = conn.execute(text("SELECT id FROM plans WHERE name = :p"), {"p": plan}).fetchone()
        if not plan_row:
            raise HTTPException(status_code=400, detail="Invalid plan name")

        conn.execute(text("""
            INSERT INTO api_keys (api_key, owner, active, created_at, expires_at, requests, plan_id)
            VALUES (:k, :o, TRUE, NOW(), NOW() + INTERVAL '90 days', 0, :pid)
        """), {"k": new_key, "o": owner, "pid": plan_row.id})

    return {"api_key": new_key, "plan": plan, "expires_in_days": 90}


@app.get("/health")
def health_check():
    return {"status": "ok"}

# ✅ ------------------ ЛОГИРОВАНИЕ ------------------

logging.basicConfig(
    filename="greencore_requests.log",
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

@app.middleware("http")
async def log_requests(request, call_next):
    start = datetime.now()
    response = await call_next(request)
    duration = (datetime.now() - start).total_seconds()
    logging.info(f"{request.client.host} | {request.method} {request.url.path} | {response.status_code} | {duration:.2f}s")
    return response

# ✅ ------------------ OPENAPI (Authorize + schemas fix) ------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return await request_validation_exception_handler(request, exc)

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title="GreenCore API",
        version="1.7.2",
        description="API с системой тарифов и авторизацией по ключу.",
        routes=app.routes,
    )

    schema["components"]["schemas"] = schema.get("components", {}).get("schemas", {})
    schema["components"]["schemas"]["HTTPValidationError"] = {
        "title": "HTTPValidationError",
        "type": "object",
        "properties": {
            "detail": {"title": "Detail", "type": "array", "items": {"$ref": "#/components/schemas/ValidationError"}}
        },
    }
    schema["components"]["schemas"]["ValidationError"] = {
        "title": "ValidationError",
        "type": "object",
        "properties": {
            "loc": {"title": "Location", "type": "array", "items": {"type": "string"}},
            "msg": {"title": "Message", "type": "string"},
            "type": {"title": "Error Type", "type": "string"},
        },
    }

    schema["components"]["securitySchemes"] = {
        "APIKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    }

    for path in schema["paths"]:
        for method in schema["paths"][path]:
            schema["paths"][path][method]["security"] = [{"APIKeyHeader": []}]

    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
