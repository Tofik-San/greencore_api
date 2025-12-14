import secrets
from datetime import datetime, timedelta

def generate_login_token() -> str:
    # криптостойкий, URL-safe
    return secrets.token_urlsafe(24)

def ttl_minutes(minutes: int = 15) -> datetime:
    return datetime.utcnow() + timedelta(minutes=minutes)
