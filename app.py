from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os
from typing import Optional, Literal
from fastapi.openapi.utils import get_openapi
import logging
from datetime import datetime
import secrets

# ✅ Загрузка .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
MASTER_KEY = os.getenv("MASTER_KEY")

app = FastAPI()

# 🌐 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(DATABASE_URL)

# 💡 Паттерны освещённости
LIGHT_PATTERNS = {
    "тень": ["full shade", "shade", "тень", "indirect", "diffused"],
    "полутень": ["part shade", "partial", "полутень", "рассеян", "утреннее"],
    "яркий": ["full sun", "sun", "прямое солнце", "яркий", "солнеч"],
}

# 🌿 Главный эндпоинт
@app.get("/plants")
def get_plants(
    view: Optional[str] = Query(None, description="Название вида или сорта растения"),
    light: Optional[Literal["тень", "полутень", "яркий"]] = Query(None, description="Освещённость"),
    zone_usda: Optional[Literal[
        "3", "4", "5",
        "6", "7", "8", "9",
        "10", "11", "12"
    ]] = Query(None, description="Климатическая зона USDA (выберите из списка)"),
    placement: Optional[Literal["комнатное", "садовое"]] = Query(None, description="Тип размещения"),
    limit: int = Query(50, ge=1, le=100, description="Количество карточек в ответе"),
):
    query = "SELECT * FROM plants WHERE 1=1"
    params: dict = {}

    # поиск по названию вида/сорта
    if view:
        query += " AND (LOWER(view) LIKE :view OR LOWER(cultivar) LIKE :view)"
        params["view"] = f"%{view.lower()}%"

    # фильтр по освещённости
    if light:
        pats = LIGHT_PATTERNS.get(light, [])
        if pats:
            clauses = []
            for i, pat in enumerate(pats):
                key = f"light_{i}"
                clauses.append(f"LOWER(light) LIKE :{key}")
                params[key] = f"%{pat.lower()}%"
            query += " AND (" + " OR ".join(clauses) + ")"

    # фильтр USDA (поддержка диапазонов и одиночных значений, устойчив к пустым)
    if zone_usda:
        z_input = zone_usda.replace("–", "-").replace("—", "-").strip()

        if "-" in z_input:
            try:
                zmin, zmax = [int(x) for x in z_input.split("-")]
                query += """
                    AND (
                        TRIM(filter_zone_usda) != ''
                        AND filter_zone_usda IS NOT NULL
                        AND (
                            filter_zone_usda = :exact
                            OR (
                                SPLIT_PART(filter_zone_usda, '-', 1)::int <= :zmax
                                AND SPLIT_PART(
                                    CASE 
                                        WHEN POSITION('-' IN filter_zone_usda) > 0 
                                        THEN filter_zone_usda 
                                        ELSE filter_zone_usda || '-' || filter_zone_usda 
                                    END, 
                                    '-', 2
                                )::int >= :zmin
                            )
                        )
                    )
                """
                params.update({"exact": z_input, "zmin": zmin, "zmax": zmax})
            except Exception:
                query += " AND filter_zone_usda LIKE :zone"
                params["zone"] = f"%{z_input}%"
        else:
            try:
                z = int(z_input)
                query += """
                    AND (
                        TRIM(filter_zone_usda) != ''
                        AND filter_zone_usda IS NOT NULL
                        AND (
                            filter_zone_usda LIKE :zone
                            OR (
                                SPLIT_PART(filter_zone_usda, '-', 1)::int <= :z
                                AND SPLIT_PART(
                                    CASE 
                                        WHEN POSITION('-' IN filter_zone_usda) > 0 
                                        THEN filter_zone_usda 
                                        ELSE filter_zone_usda || '-' || filter_zone_usda 
                                    END, 
                                    '-', 2
                                )::int >= :z
                            )
                        )
                    )
                """
                params.update({"zone": f"%{z_input}%", "z": z})
            except Exception:
                query += " AND filter_zone_usda LIKE :zone"
                params["zone"] = f"%{z_input}%"

    # фильтр по типу размещения
    if placement:
        if placement == "комнатное":
            query += " AND indoor = true"
        elif placement == "садовое":
            query += " AND outdoor = true"

    query += " ORDER BY id LIMIT :limit"
    params["limit"] = limit

    with engine.connect() as connection:
        result = connection.execute(text(query), params)
        plants = [dict(row._mapping) for row in result]

    return {"count": len(plants), "limit": limit, "results": plants}


@app.get("/plant/{plant_id}")
def get_plant(plant_id: int):
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM plants WHERE id = :id"), {"id": plant_id}
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Plant not found")
    return dict(row._mapping)


@app.get("/stats")
def get_stats():
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
            SELECT 
                COUNT(*) AS total,
                COUNT(DISTINCT view) AS unique_views,
                COUNT(DISTINCT family) AS unique_families,
                SUM(CASE WHEN toxicity = 'toxic' THEN 1 ELSE 0 END) AS toxic_count,
                SUM(CASE WHEN beginner_friendly = true THEN 1 ELSE 0 END) AS beginner_friendly_count
            FROM plants;
        """
            )
        ).fetchone()
    return dict(row._mapping)


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ✅ Логирование
logging.basicConfig(
    filename="greencore_requests.log",
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

@app.middleware("http")
async def log_requests(request, call_next):
    start_time = datetime.now()
    response = await call_next(request)
    duration = (datetime.now() - start_time).total_seconds()
    log_line = (
        f"{request.client.host} | {request.method} {request.url.path} "
        f"| status {response.status_code} | time {duration:.2f}s"
    )
    if request.query_params:
        log_line += f" | params: {dict(request.query_params)}"
    logging.info(log_line)
    return response


# 📘 Swagger
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title="GreenCore API",
        version="1.7.0",
        description="Окончательная логика USDA: поддержка диапазонов и одиночных значений.",
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["APIKeyHeader"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }
    for path in schema["paths"]:
        for method in schema["paths"][path]:
            schema["paths"][path][method]["security"] = [{"APIKeyHeader": []}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi


# ✅ API KEYS SYSTEM
@app.post("/generate_key")
def generate_api_key(x_api_key: str = Header(...), owner: Optional[str] = "user"):
    if x_api_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Access denied: admin key required")

    new_key = secrets.token_hex(32)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id SERIAL PRIMARY KEY,
                api_key TEXT UNIQUE NOT NULL,
                owner TEXT,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                expires_at TIMESTAMP,
                requests INT DEFAULT 0
            );
        """))

        conn.execute(
            text("""
                INSERT INTO api_keys (api_key, owner, expires_at)
                VALUES (:k, :o, NOW() + INTERVAL '90 days')
            """),
            {"k": new_key, "o": owner}
        )

    return {"api_key": new_key, "expires_in_days": 90}


@app.middleware("http")
async def verify_dynamic_api_key(request, call_next):
    open_paths = ["/docs", "/openapi.json", "/health", "/generate_key"]
    if any(request.url.path.startswith(p) for p in open_paths):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT active, expires_at FROM api_keys WHERE api_key = :key"),
            {"key": api_key}
        ).fetchone()

    if not row or not row[0]:
        raise HTTPException(status_code=403, detail="Invalid or inactive API key")

    response = await call_next(request)

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE api_keys SET requests = requests + 1 WHERE api_key = :key"),
            {"key": api_key}
        )

    return response
