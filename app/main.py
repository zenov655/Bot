import os
import sys
import asyncio

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from aiogram.types import Update

from app.bot import bot, dp
from app.config import settings


app = FastAPI()


def _build_webhook_url() -> str:
    base = (settings.webhook_url or "").strip()
    if not base:
        return ""
    if base.endswith("/telegram-webhook"):
        return base
    if base.endswith("/telegram-webhook/"):
        return base.rstrip("/")
    return base.rstrip("/") + "/telegram-webhook"


@app.on_event("startup")
async def _on_startup() -> None:
    webhook_url = _build_webhook_url()
    if webhook_url:
        await bot.set_webhook(webhook_url)


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    await bot.session.close()


@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}


@app.get("/")
async def healthcheck():
    return {"status": "ok"}


if __name__ == "__main__":
    if settings.webhook_url:
        import uvicorn

        port = int(os.environ.get("PORT", "8000"))
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        async def _run_polling() -> None:
            await bot.delete_webhook(drop_pending_updates=True)
            try:
                await dp.start_polling(bot)
            finally:
                await bot.session.close()

        asyncio.run(_run_polling())
