if __name__ == "__main__":
    import asyncio
    import logging
    import os
    import sys

    from app.services.billing import billing_engine
    import app.panel_client as panel_client

    import urllib3
    from aiogram import Bot
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    from app.logging_sanitizer import ensure_sanitized_logging
    ensure_sanitized_logging()

    from app.bot.dispatcher import dp
    from app.config import ADMIN_ID, BOT_TOKEN, VLESS_SID
    from app.db import init_db, close_db
    from app.services.notifications import check_notifications


    def _log_task_exception(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logging.exception("Background task failed")

    async def main():
        required = ["BOT_TOKEN", "ADMIN_ID", "PANEL_URL", "PANEL_USER", "PANEL_PASS", "SERVER_IP", "VLESS_PBK", "PAY_TOKEN"]
        for var in required:
            if not os.getenv(var):
                logging.critical("Критическая ошибка: переменная %s не задана! Бот не запустится.", var)
                sys.exit(1)

        if ADMIN_ID <= 0:
            logging.critical("Критическая ошибка: ADMIN_ID должен быть больше нуля!")
            sys.exit(1)

        if not VLESS_SID:
            logging.warning("VLESS_SID не задан. Если REALITY требует SID, ключи могут не работать.")

        await init_db()
        bot = Bot(token=BOT_TOKEN)

        scheduler = AsyncIOScheduler()
        scheduler.add_job(check_notifications, "interval", minutes=10, args=[bot])
        scheduler.start()

        startup_check_task = asyncio.create_task(check_notifications(bot))
        startup_check_task.add_done_callback(_log_task_exception)

        # ИСПРАВЛЕНО: передаём bot до старта, иначе уведомления молча не работают
        billing_engine.set_bot(bot)
        billing_engine.start()

        logging.info("MetronVPN запущен и готов к работе.")
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        finally:
            logging.info("Начинаем graceful shutdown...")
            billing_engine.stop()
            await bot.session.close()
            await panel_client.close_panel_session()
            if scheduler.running:
                scheduler.shutdown(wait=False)
            if not startup_check_task.done():
                startup_check_task.cancel()
            await close_db()

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Работа бота завершена.")