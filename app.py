from fastapi import FastAPI, Depends, Header, HTTPException
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

# 🌱 Эндпоинт: Получение растений
@app.get("/plants", dependencies=[Depends(verify_api_key)])
def get_plants():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT * FROM plants"))
        plants = [dict(row._mapping) for row in result]
        return plants

# ❤️ Эндпоинт: Проверка состояния
@app.get("/health", dependencies=[Depends(verify_api_key)])
def health_check():
    return {"status": "ok"}

# 🧪 Настройка Swagger с авторизацией
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="GreenCore API",
        version="1.0.0",
        description="API для доступа к базе растений с защитой по X-API-Key",
        routes=app.routes,
    )

    # ✅ Исправление KeyError
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
