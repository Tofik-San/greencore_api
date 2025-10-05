
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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DB connection
engine = create_engine(DATABASE_URL)

# 🔐 Проверка API-ключа
def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API Key")

# 🌱 Эндпоинт: Получение растений с фильтрами
@app.get("/plants", dependencies=[Depends(verify_api_key)])
def get_plants(
    view: Optional[str] = Query(None),
    light: Optional[str] = Query(None),
    beginner_friendly: Optional[bool] = Query(None),
    toxicity: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100)
    ):
    query = "SELECT * FROM plants WHERE 1=1"
    params = {}

    if view:
    query += " AND LOWER(view) LIKE :view"
    params["view"] = f"%{view.lower()}%"

    if light:
    query += " AND LOWER(light) LIKE :light"
    params["light"] = f"%{light.lower()}%"

    if beginner_friendly is not None:
    query += " AND beginner_friendly = :beginner_friendly"
    params["beginner_friendly"] = beginner_friendly

    if toxicity:
    query += " AND LOWER(toxicity) = :toxicity"
    params["toxicity"] = toxicity.lower()

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

    # ❤️ Эндпоинт: Проверка состояния
    @app.get("/health", dependencies=[Depends(verify_api_key)])
    def health_check():
    return {"status": "ok"}

    # 🧪 Swagger авторизация
    def custom_openapi():
    if app.openapi_schema:
    return app.openapi_schema

    openapi_schema = get_openapi(
    title="GreenCore API",
    version="1.0.0",
    description="API для доступа к базе растений с защитой по X-API-Key",
    routes=app.routes,
    )

    if "components" not in openapi_schema:
    openapi_schema["components"] = {}
    if "securitySchemes" not in openapi_schema["components"]:
    openapi_schema["components"]["securitySchemes"] = {}

    openapi_schema["components"]["securitySchemes"]["APIKeyHeader"] = {
    "type": "apiKey",
    "in": "header",
    "name": "X-API-Key"
    }

    for path in openapi_schema["paths"]:
    for method in openapi_schema["paths"][path]:
    openapi_schema["paths"][path][method]["security"] = [{"APIKeyHeader": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema

    app.openapi = custom_openapi