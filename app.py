from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import JSONResponse
from starlette.requests import Request
from typing import Optional
from dotenv import load_dotenv
import os

# --- загрузка переменных окружения ---
load_dotenv()

# --- базовая инициализация приложения ---
app = FastAPI(
    title="GreenCore API",
    description="API для работы с базой растений GreenCore",
    version="1.9"
)

# --- CORS: берём разрешённые источники из .env ---
origins = os.getenv("CORS_ORIGINS", "").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API key middleware ---
API_KEY_NAME = "X-API-Key"  # фикс регистра
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def verify_api_key(request: Request, api_key: Optional[str] = Depends(api_key_header)):
    valid_key = os.getenv("API_KEY")
    if valid_key and api_key != valid_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный API-ключ"
        )

# --- Healthcheck ---
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# --- Пример защищённого эндпоинта ---
@app.get("/v1/plants", dependencies=[Depends(verify_api_key)])
async def get_plants(limit: int = 10):
    # пример фиктивных данных; в реальности подключается база PostgreSQL
    data = [{"id": i, "name": f"Plant {i}"} for i in range(limit)]
    return {"count": len(data), "results": data}

# --- Генерация ключа (демо) ---
@app.get("/generate_key")
async def generate_key():
    return {"key": "demo-key-123"}

# --- Обработчик 404 ---
@app.exception_handler(404)
async def not_found(request: Request, exc):
    return JSONResponse(status_code=404, content={"error": "not found"})

# --- Корень ---
@app.get("/")
async def root():
    return {"message": "GreenCore API is running"}
