from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os

# Загрузка переменных окружения из .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
print("🚨 DATABASE_URL:", DATABASE_URL)


# Создание движка
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
