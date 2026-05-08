from apscheduler.schedulers.asyncio import AsyncIOScheduler
from utils.logger import logger


def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone="Europe/Kaliningrad")


def schedule_check(scheduler: AsyncIOScheduler, job_func, interval_minutes: int) -> None:
    scheduler.add_job(
        job_func,
        trigger="interval",
        minutes=interval_minutes,
        id="afisha_check",
        replace_existing=True,
    )
    logger.info(f"Scheduled check every {interval_minutes} minutes")
