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

# 🌐 CORS (ограничить позже на прод)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # на релизе заменить на конкретный домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🔗 Подключение к БД
engine = create_engine(DATABASE_URL)

# 🔐 Проверка API-ключа
def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API Key")

# 🌿 Основной эндпоинт
@app.get("/plants", dependencies=[Depends(verify_api_key)])
def get_plants(
    view: Optional[str] = Query(None, description="Название вида"),
    light: Optional[str] = Query(
        None,
        enum=["тень", "полутень", "яркий"],
        description="Освещение: тень | полутень | яркий"
    ),
    beginner_friendly: Optional[str] = Query(
        None,
        enum=["да", "нет"],
        description="Подходит новичкам: да | нет"
    ),
    toxicity: Optional[str] = Query(
        None,
        enum=["нет", "умеренно", "токсично"],
        description="Токсичность: нет | умеренно | токсично"
    ),
    temperature: Optional[str] = Query(None, description="Температурный диапазон (например 18–25)"),
    ru_regions: Optional[str] = Query(None, description="Регионы РФ (RU-MOW|RU-KDA и т.д.)"),
    indoor: Optional[str] = Query(
        None,
        enum=["true", "false"],
        description="Комнатное растение: true | false"
    ),
    outdoor: Optional[str] = Query(
        None,
        enum=["true", "false"],
        description="Уличное растение: true | false"
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100)
):
    query = "SELECT * FROM plants WHERE 1=1"
    params = {}

    # 🌿 View
    if view:
        query += " AND LOWER(view) LIKE :view"
        params["view"] = f"%{view.lower()}%"

    # 💡 Light
    if light:
        light_map = {"тень": "shade", "полутень": "partial", "яркий": "sun"}
        query += " AND LOWER(light) LIKE :light"
        params["light"] = f"%{light_map[light]}%"

    # 🌱 Beginner friendly
    if beginner_friendly:
        bf_map = {"да": True, "нет": False}
        query += " AND beginner_friendly = :bf"
        params["bf"] = bf_map[beginner_friendly]

    # ☠️ Toxicity
    if toxicity:
        tox_map = {"нет": "none", "умеренно": "mild", "токсично": "toxic"}
        query += " AND LOWER(toxicity) = :tox"
        params["tox"] = tox_map[toxicity]

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

# 🔎 Детальная карточка
@app.get("/plant/{plant_id}", dependencies=[Depends(verify_api_key)])
def get_plant(plant_id: int):
    with engine.connect() as connection:
        result = connection.execute(text("SELECT * FROM plants WHERE id = :id"), {"id": plant_id}).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Plant not found")
    return dict(result._mapping)

# 📊 Статистика по базе
@app.get("/stats", dependencies=[Depends(verify_api_key)])
def get_stats():
    with engine.connect() as connection:
        query = """
        SELECT 
            COUNT(*) AS total,
            COUNT(DISTINCT view) AS unique_views,
            COUNT(DISTINCT family) AS unique_families,
            SUM(CASE WHEN toxicity = 'toxic' THEN 1 ELSE 0 END) AS toxic_count,
            SUM(CASE WHEN beginner_friendly = true THEN 1 ELSE 0 END) AS beginner_friendly_count
        FROM plants;
        """
        result = connection.execute(text(query)).fetchone()
    return dict(result._mapping)

# 🩺 Health-check
@app.get("/health", dependencies=[Depends(verify_api_key)])
def health_check():
    return {"status": "ok"}

# 📘 Swagger с API-ключом
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title="GreenCore API",
        version="1.2.0",
        description="API для доступа к базе растений с фиксированными фильтрами, защитой по X-API-Key и статистикой",
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
