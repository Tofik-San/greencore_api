# utils/notify.py
import os
import json
from datetime import datetime
import httpx

# ─────────────────────────────────────────────
# Конфигурация
# ─────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ─────────────────────────────────────────────
# Основная функция уведомлений
# ─────────────────────────────────────────────
async def send_alert(
    event_type: str,
    detail: dict | str,
    user_key: str | None = None,
    endpoint: str | None = None,
    status_code: int | None = None,
):
    """Отправка push-уведомлений о сбоях, ошибках, лимитах и тестах."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[NotifyError] Переменные TELEGRAM_TOKEN или TELEGRAM_CHAT_ID не заданы")
        return

    # формируем текст
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
    payload = {
        "chat_id": int(TELEGRAM_CHAT_ID),
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(url, json=payload)
            if r.status_code != 200:
                print(f"[NotifyError] Telegram API error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[NotifyError] {e}")
