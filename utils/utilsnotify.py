# utils/notify.py
import os, json
from datetime import datetime
import httpx

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

async def send_alert(event_type: str, detail: dict | str,
                     user_key: str | None = None, endpoint: str | None = None,
                     status_code: int | None = None):
    """Зови ТОЛЬКО при отклонениях: 5xx, invalid key, rate limit."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    if isinstance(detail, dict):
        detail_text = json.dumps(detail, ensure_ascii=False)
    else:
        detail_text = str(detail)

    text = (
        "⚠️ *GreenCore API Alert*\n"
        f"*Тип:* {event_type}\n"
        f"*Время:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"*Ключ:* {user_key or '-'}\n"
        f"*Endpoint:* {endpoint or '-'}\n"
        f"*Статус:* {status_code or '-'}\n"
        f"*Детали:* {detail_text}"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": int(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "Markdown"}

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json=payload)
    except Exception:
        # не роняем API, просто молчим
        pass
