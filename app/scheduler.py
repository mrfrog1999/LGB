"""定时任务：每天在 FETCH_TIME 抓取全部订阅产品并推送邮件。"""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db, service
from .config import settings

log = logging.getLogger("scheduler")
_scheduler: AsyncIOScheduler | None = None


async def _daily_job() -> None:
    log.info("定时任务开始：同步产品收益率")
    try:
        results = await asyncio.to_thread(service.sync_all)
        ok = sum(1 for r in results if "error" not in r)
        log.info("同步完成：%s/%s 成功", ok, len(results))
        try:
            await asyncio.to_thread(service.send_daily_mail)
            log.info("日报已推送")
        except Exception as exc:  # noqa: BLE001
            db.add_log("error", f"日报推送失败：{exc}")
            log.warning("日报推送失败：%s", exc)
    except Exception as exc:  # noqa: BLE001
        db.add_log("error", f"定时任务异常：{exc}")
        log.exception("定时任务异常")


def start() -> AsyncIOScheduler:
    global _scheduler
    hour, minute = settings.fetch_hour_minute
    _scheduler = AsyncIOScheduler(timezone=settings.TZ)
    _scheduler.add_job(
        _daily_job,
        CronTrigger(hour=hour, minute=minute, timezone=settings.TZ),
        id="daily_fetch",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    _scheduler.start()
    log.info("定时任务已注册：每天 %02d:%02d（%s）", hour, minute, settings.TZ)
    return _scheduler


def shutdown() -> None:
    if _scheduler:
        _scheduler.shutdown(wait=False)


def next_run() -> str | None:
    if not _scheduler:
        return None
    job = _scheduler.get_job("daily_fetch")
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M")
    return None
