import os
import psycopg2
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.status import HTTP_401_UNAUTHORIZED

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("API_KEY")

synonyms = {
    "light": {
        "тень": "shade",
        "полутень": "shade",
        "рассеянный": "shade",
        "солнце": "sun",
        "солнечно": "sun",
        "яркий свет": "sun",
        "прямое солнце": "sun",
        "свет": "sun",
        "частичное солнце": "partial",
        "утреннее солнце": "partial"
    },
    "toxicity": {
        "нет": "none",
        "none": "none",
        "умеренно": "mild",
        "mild": "mild",
        "ядовито": "toxic",
        "токсично": "toxic"
    },
    "beginner_friendly": {
        "да": True,
        "true": True,
        "нет": False,
        "false": False
    }
}

def apply_synonyms(param: str, category: str):
    value = param.lower()
    return synonyms.get(category, {}).get(value, value)

@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    if request.url.path.startswith("/plants"):
        api_key = request.headers.get("X-API-Key")
        if api_key != API_KEY:
            return JSONResponse(status_code=HTTP_401_UNAUTHORIZED, content={"detail": "Invalid or missing API key"})
    return await call_next(request)

@app.get("/plants")
def get_plants(view: str = None, light: str = None, toxicity: str = None, beginner_friendly: str = None, temperature: str = None, page: int = 1, limit: int = 10):
    offset = (page - 1) * limit
    
    query = "SELECT * FROM plants WHERE 1=1"
    params = {}

    if view:
        query += " AND LOWER(view) LIKE %(view)s"
        params["view"] = f"%{view.lower()}%"

    if light:
        norm = apply_synonyms(light, "light")
        query += " AND LOWER(light) LIKE %(light)s"
        params["light"] = f"%{norm}%"

    if toxicity:
        norm = apply_synonyms(toxicity, "toxicity")
        query += " AND LOWER(toxicity) = %(toxicity)s"
        params["toxicity"] = norm

    if beginner_friendly:
        norm = apply_synonyms(beginner_friendly, "beginner_friendly")
        query += " AND beginner_friendly = %(beginner_friendly)s"
        params["beginner_friendly"] = norm

    if temperature:
        query += " AND temperature LIKE %(temperature)s"
        params["temperature"] = f"%{temperature}%"

    query += " LIMIT %(limit)s OFFSET %(offset)s"
    params["limit"] = limit
    params["offset"] = offset

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute(query, params)
    columns = [desc[0] for desc in cur.description]
    results = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()

    return results

@app.get("/health")
def health_check():
    return {"status": "ok"}