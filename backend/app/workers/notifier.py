"""Фоновые уведомления в Discord / Telegram.

Может работать:
- внутри API (NOTIFY_SCHEDULER_ENABLED=true, по умолчанию);
- отдельным процессом: python -m app.workers.notifier.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import timedelta

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.ingestion.classifier import STATUS_LABELS, THREAT_LABELS
from app.models import Alert, Region
from app.utils.time import utcnow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("notifier")

SPACE_RE = re.compile(r"\s+")
EMOJI = {"active": "🔴", "warning": "🟡", "all_clear": "🟢", "cleared": "⚪"}


def _snippet(text: str, limit: int = 500) -> str:
    return SPACE_RE.sub(" ", text or "").strip()[:limit]


def format_alert(alert: Alert, *, html_mode: bool = False) -> str:
    """Текст уведомления. html_mode — для Telegram parse_mode=HTML."""
    region = alert.region.name if alert.region else "Регион не определён"
    status = STATUS_LABELS.get(alert.status, alert.status)
    threat = THREAT_LABELS.get(alert.threat_type, alert.threat_type)
    emoji = EMOJI.get(alert.status, "⚪")
    event_time = alert.event_at.strftime("%d.%m.%Y %H:%M UTC")

    if html_mode:
        region = html.escape(region)
        status = html.escape(status)
        threat = html.escape(threat)
        snippet = html.escape(_snippet(alert.message))
    else:
        snippet = _snippet(alert.message)

    lines = [
        f"{emoji} {status} — {region}",
        f"Тип: {threat}",
        f"Время: {event_time}",
    ]
    if alert.merged_count > 1:
        lines.append(f"Подтверждений: {alert.merged_count}")
    lines += ["", snippet]

    if alert.sources:
        url = next((link.message_url for link in alert.sources if link.message_url), None)
        if url:
            lines += ["", f"Источник: {url}"]
    return "\n".join(lines)


async def _post_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> bool:
    """POST с экспоненциальным retry (1→2→4 c)."""
    delay = 1.0
    for attempt in range(1, 4):
        try:
            response = await client.post(url, **kwargs)
            if response.status_code == 429:
                wait = float(response.headers.get("Retry-After", delay))
                logger.warning("Rate limit %s, пауза %s c", url, wait)
                await asyncio.sleep(wait)
                continue
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("Попытка %s/3 для %s не удалась: %s", attempt, url, exc)
            if attempt < 3:
                await asyncio.sleep(delay)
                delay *= 2
    return False


async def dispatch_alert(alert: Alert) -> bool:
    """Отправляет алерт во все настроенные каналы. True — хотя бы один успешен."""
    sent = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0)) as client:
        if settings.discord_webhook_url:
            payload = {"content": format_alert(alert)[:1900], "username": "Drone Alert Radar"}
            sent = await _post_with_retry(client, settings.discord_webhook_url, json=payload) or sent

        if settings.telegram_bot_token and settings.telegram_bot_chat_id:
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": settings.telegram_bot_chat_id,
                "text": format_alert(alert, html_mode=True)[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            sent = await _post_with_retry(client, url, json=payload) or sent
    return sent


async def poll_and_notify(session: AsyncSession | None = None) -> int:
    """Забирает неотправленные алерты и шлёт уведомления."""
    if not settings.notify_configured:
        return 0

    own_session = session is None
    db = session or SessionLocal()
    sent_count = 0
    try:
        cutoff = utcnow() - timedelta(minutes=settings.notify_window_minutes)
        statuses = ("active", "warning")
        if settings.notify_all_clear:
            statuses = ("active", "warning", "all_clear")

        stmt = (
            select(Alert)
            .where(
                Alert.notified_at.is_(None),
                Alert.event_at >= cutoff,
                Alert.status.in_(statuses),
            )
            .order_by(Alert.event_at.asc())
            .limit(50)
        )
        if settings.notify_region_list:
            stmt = stmt.where(Alert.region.has(Region.code.in_(settings.notify_region_list)))

        alerts = list((await db.scalars(stmt)).all())
        for alert in alerts:
            try:
                if await dispatch_alert(alert):
                    alert.notified_at = utcnow()
                    await db.commit()
                    sent_count += 1
            except Exception:  # noqa: BLE001 — один канал не должен останавливать остальные
                logger.exception("Ошибка отправки алерта #%s", alert.id)
    finally:
        if own_session:
            await db.close()

    if sent_count:
        logger.info("Отправлено уведомлений: %s", sent_count)
    return sent_count


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        poll_and_notify,
        trigger="interval",
        seconds=max(5, settings.notify_poll_seconds),
        id="poll-alerts",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    return scheduler


async def main() -> None:
    if not settings.notify_configured:
        logger.error("Notifier не настроен: добавьте DISCORD_WEBHOOK_URL или Telegram-бота")
        return
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Notifier запущен, интервал %s c", settings.notify_poll_seconds)
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
