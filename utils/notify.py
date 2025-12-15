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
        f"⚠️ <b>GreenCore API Alert</b>\n"
        f"<b>Тип:</b> {event_type}\n"
        f"<b>Время:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"<b>Ключ:</b> {user_key or '-'}\n"
        f"<b>Endpoint:</b> {endpoint or '-'}\n"
        f"<b>Статус:</b> {status_code or '-'}\n"
        f"<b>Детали:</b> {detail_text}"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": int(TELEGRAM_CHAT_ID),
        "text": text,
        "parse_mode": "HTML",  # ✅ безопасный режим
    }

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(url, json=payload)
            if r.status_code != 200:
                print(f"[NotifyError] Telegram API error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[NotifyError] {e}")
# ── Email via Resend ───────────────────────────────────────────────────────────
import resend

def send_api_key_email(email: str, api_key: str, plan: str) -> bool:
    """
    Отправляет пользователю письмо с API-ключом после оплаты.
    Возвращает True при успешной отправке, False при ошибке.
    """
    try:
        resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
        if not resend_api_key:
            print("[EmailError] RESEND_API_KEY not set; skip send")
            return False

        resend.api_key = resend_api_key

        from_addr = os.getenv(
            "FROM_EMAIL",
            "GreenCore <noreply@greencore-api.ru>"
        ).strip()

        html = f"""
        <div style="font-family:system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; line-height:1.6;">
          <h2 style="color:#16a34a;margin:0 0 12px;">GreenCore — ваш API-ключ</h2>
          <p style="margin:0 0 10px;">Тариф: <b>{plan}</b></p>
          <p style="margin:0 0 10px;">Ваш API-ключ:</p>
          <pre style="background:#0b0b0b;color:#a7f3d0;padding:12px;border:1px solid #16a34a;white-space:pre-wrap;word-break:break-all;border-radius:8px;">{api_key}</pre>
          <p style="margin:12px 0 0;color:#9ca3af;font-size:12px;">
            Если вы не совершали эту операцию, проигнорируйте письмо.
          </p>
        </div>
        """

        resend.emails.send(
            from_=from_addr,
            to=email,
            subject="Ваш API-ключ GreenCore",
            html=html,
        )
        return True

    except Exception as e:
        print(f"[EmailError] send_api_key_email failed: {e}")
        return False
