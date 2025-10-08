from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os
from typing import Optional, Literal
from fastapi.openapi.utils import get_openapi

load_dotenv()

API_KEY = os.getenv("API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

app = FastAPI()

# 🌐 CORS (на прод ограничить доменом)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(DATABASE_URL)

# 🔐 Проверка API-ключа
def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API Key")

# 🌡 Нормализация temperature
def norm_temp_sql(field: str = "temperature") -> str:
    return (
        "LOWER("
        f"REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({field}, '°', ''),'c',''),' ',''),'–','-'),'—','-')"
        ")"
    )

# 💡 Паттерны освещённости (рус/англ)
LIGHT_PATTERNS = {
    "тень": ["full shade", "shade", "тень", "indirect", "diffused"],
    "полутень": ["part shade", "partial", "полутень", "рассеян", "утреннее"],
    "яркий": ["full sun", "sun", "прямое солнце", "яркий", "солнеч"],
}

@app.get("/plants", dependencies=[Depends(verify_api_key)])
def get_plants(
    search_field: Optional[Literal["view", "cultivar"]] = Query(
        "view", description="Выбор поля для поиска: view (вид) или cultivar (сорт)"
    ),
    view: Optional[str] = Query(None, description="Название вида или сорта растения"),
    light: Optional[Literal["тень", "полутень", "яркий"]] = Query(None, description="Освещённость"),
    temperature: Optional[str] = Query(None, description="Температурный диапазон (например 18–25)"),
    toxicity: Optional[Literal["нет", "умеренно", "токсично"]] = Query(None, description="Токсичность"),
    beginner_friendly: Optional[Literal["да", "нет"]] = Query(None, description="Подходит новичкам"),
    placement: Optional[Literal["комнатное", "садовое"]] = Query(None, description="Тип размещения: комнатное или садовое"),
    limit: int = Query(50, ge=1, le=100, description="Количество карточек в ответе (по умолчанию 50)"),
):
    query = "SELECT * FROM plants WHERE 1=1"
    params: dict = {}

    # 🌿 Поиск по виду или сорту
    if view:
        if search_field == "view":
            query += " AND LOWER(view) LIKE :view"
        elif search_field == "cultivar":
            query += " AND LOWER(cultivar) LIKE :view"
        params["view"] = f"%{view.lower()}%"

    # 💡 Light
    if light:
        pats = LIGHT_PATTERNS.get(light, [])
        if pats:
            clauses = []
            for i, pat in enumerate(pats):
                key = f"light_{i}"
                clauses.append(f"LOWER(light) LIKE :{key}")
                params[key] = f"%{pat.lower()}%"
            query += " AND (" + " OR ".join(clauses) + ")"

    # 🌡 Temperature
    if temperature:
        t = (
            temperature.lower()
            .replace("°", "")
            .replace("c", "")
            .replace(" ", "")
            .replace("–", "-")
            .replace("—", "-")
        )
        query += f" AND {norm_temp_sql('temperature')} LIKE :temp"
        params["temp"] = f"%{t}%"

    # ☠️ Toxicity
    if toxicity:
        tox_map = {"нет": "none", "умеренно": "mild", "токсично": "toxic"}
        query += " AND LOWER(toxicity) = :tox"
        params["tox"] = tox_map[toxicity]

    # 🌱 Beginner-friendly
    if beginner_friendly:
        query += " AND beginner_friendly = :bf"
        params["bf"] = (beginner_friendly == "да")

    # 🏡 Placement
    if placement:
        if placement == "комнатное":
            query += " AND indoor = true"
        elif placement == "садовое":
            query += " AND outdoor = true"

    # 🔢 Ограничение
    query += " ORDER BY id LIMIT :limit"
    params["limit"] = limit

    with engine.connect() as connection:
        result = connection.execute(text(query), params)
        plants = [dict(row._mapping) for row in result]

    return {"count": len(plants), "limit": limit, "results": plants}

@app.get("/plant/{plant_id}", dependencies=[Depends(verify_api_key)])
def get_plant(plant_id: int):
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM plants WHERE id = :id"), {"id": plant_id}
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Plant not found")
    return dict(row._mapping)

@app.get("/stats", dependencies=[Depends(verify_api_key)])
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

@app.get("/health", dependencies=[Depends(verify_api_key)])
def health_check():
    return {"status": "ok"}

# 📘 Swagger
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title="GreenCore API",
        version="1.6.1",
        description="Выбор поля поиска (view/cultivar) + основные фильтры: light, temperature, toxicity, beginner_friendly, placement",
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
