from fastapi import FastAPI, Query, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")
API_KEY = os.getenv("API_KEY")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/plants")
def get_plants(
    request: Request,
    x_api_key: str = Header(...),
    view: str = Query(None),
    light: str = Query(None),
    beginner_friendly: str = Query(None),
    toxicity: str = Query(None),
    limit: int = Query(10),
    page: int = Query(1)
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    offset = (page - 1) * limit

    light_synonyms = {
        "shade": ["shade", "полутень", "рассеянный свет", "тень"],
    }
    toxicity_synonyms = {
        "none": ["нет", "none", "безопасно"],
        "mild": ["умеренно", "mild"],
        "toxic": ["токсично", "ядовито", "toxic"]
    }

    def normalize(value, synonyms_dict):
        if not value:
            return None
        value = value.lower()
        for key, synonyms in synonyms_dict.items():
            if value in synonyms:
                return key
        return value

    light_normalized = normalize(light, light_synonyms)
    toxicity_normalized = normalize(toxicity, toxicity_synonyms)

    query = "SELECT * FROM plants WHERE 1=1"
    params = {}

    if view:
        query += " AND LOWER(view) LIKE %(view)s"
        params["view"] = f"%{view.lower()}%"
    if light_normalized:
        query += " AND LOWER(light_enum) = %(light)s"
        params["light"] = light_normalized
    if beginner_friendly is not None:
        if beginner_friendly.lower() in ["true", "1", "да"]:
            query += " AND beginner_friendly = true"
        elif beginner_friendly.lower() in ["false", "0", "нет"]:
            query += " AND beginner_friendly = false"
    if toxicity_normalized:
        query += " AND LOWER(toxicity) = %(toxicity)s"
        params["toxicity"] = toxicity_normalized

    query += " ORDER BY id LIMIT %(limit)s OFFSET %(offset)s"
    params["limit"] = limit
    params["offset"] = offset

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return results
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})