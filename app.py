from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os
from typing import Optional
from fastapi.openapi.utils import get_openapi

load_dotenv()

API_KEY = os.getenv("API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

app = FastAPI()

# CORS — ограничить потом на релизе
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # на прод — заменить на домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(DATABASE_URL)

# 🔐 Проверка ключа
def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API Key")

# 🌗 Синонимы
synonyms = {
    "light": {
        "shade": ["тень", "полутень", "рассеянный", "рассеянный свет"],
        "sun": ["солнце", "полное солнце", "солнечно", "яркий свет", "прямое солнце"],
        "partial": ["частичное солнце", "частичное освещение", "утреннее солнце"]
    },
    "toxicity": {
        "none": ["нет", "безвредно", "не токсично", "нетоксично"],
        "mild": ["умеренно", "умеренно токсичен"],
        "toxic": ["ядовито", "токсично", "опасно"]
    },
    "beginner_friendly": {
        "true": ["да", "true", "yes"],
        "false": ["нет", "false", "no"]
    }
}

def match_synonym(value: str, group: str):
    """Возвращает нормализованное значение по словарю синонимов"""
    for key, words in synonyms.get(group, {}).items():
        if value.lower() in words or value.lower() == key:
            return key
    return None

@app.get("/plants", dependencies=[Depends(verify_api_key)])
def get_plants(
    view: Optional[str] = Query(None),
    light: Optional[str] = Query(None),
    beginner_friendly: Optional[str] = Query(None),
    toxicity: Optional[str] = Query(None),
    temperature: Optional[str] = Query(None),
    ru_regions: Optional[str] = Query(None),
    indoor: Optional[str] = Query(None),
    outdoor: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100)
):
    query = "SELECT * FROM plants WHERE 1=1"
    params = {}

    # 🔍 View
    if view:
        query += " AND LOWER(view) LIKE :view"
        params["view"] = f"%{view.lower()}%"

    # 💡 Light
    if light:
        matched = match_synonym(light, "light")
        if matched:
            query += " AND LOWER(light) LIKE :light"
            params["light"] = f"%{matched}%"

    # 🌱 Beginner
    if beginner_friendly:
        matched = match_synonym(beginner_friendly, "beginner_friendly")
        if matched:
            query += " AND beginner_friendly = :beginner"
            params["beginner"] = matched == "true"

    # ☠️ Toxicity
    if toxicity:
        matched = match_synonym(toxicity, "toxicity")
        if matched:
            query += " AND LOWER(toxicity) = :tox"
            params["tox"] = matched

    # 🌡 Temperature
    if temperature:
        query += " AND LOWER(temperature) LIKE :temp"
        params["temp"] = f"%{temperature.lower()}%"

    # 🌍 Regions
    if ru_regions:
        query += " AND LOWER(ru_regions) LIKE :region"
        params["region"] = f"%{ru_regions.lower()}%"

    # 🏡 Indoor/Outdoor
    if indoor:
        query += " AND indoor = :indoor"
        params["indoor"] = indoor.lower() == "true"
    if outdoor:
        query += " AND outdoor = :outdoor"
        params["outdoor"] = outdoor.lower() == "true"

    query += " ORDER BY id"
    offset = (page - 1) * limit
    query += " LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset

    with engine.connect() as connection:
        result = connection.execute(text(query), params)
        plants = [dict(row._mapping) for row in result]

    return {
        "count": len(plants),
        "page": page,
        "limit": limit,
        "results": plants
    }

@app.get("/plant/{plant_id}", dependencies=[Depends(verify_api_key)])
def get_plant(plant_id: int):
    """Детальная карточка по ID"""
    with engine.connect() as connection:
        result = connection.execute(text("SELECT * FROM plants WHERE id = :id"), {"id": plant_id}).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Plant not found")
    return dict(result._mapping)

@app.get("/health", dependencies=[Depends(verify_api_key)])
def health_check():
    return {"status": "ok"}

# 🧪 Swagger
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title="GreenCore API",
        version="1.1.0",
        description="API для доступа к базе растений с расширенными фильтрами и защитой по X-API-Key",
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["APIKeyHeader"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key"
    }
    for path in schema["paths"]:
        for method in schema["paths"][path]:
            schema["paths"][path][method]["security"] = [{"APIKeyHeader": []}]
    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
