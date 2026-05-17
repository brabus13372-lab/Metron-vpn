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

    # Отключаем предупреждения о небезопасном соединении
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    from app.logging_sanitizer import ensure_sanitized_logging
    ensure_sanitized_logging()

    from app.bot.dispatcher import dp
    from app.config import ADMIN_ID, BOT_TOKEN, VLESS_SID
    from app.db import init_db
    from app.services.notifications import check_expirations

    async def main():
        required = ["BOT_TOKEN", "ADMIN_ID", "PANEL_URL", "PANEL_USER", "PANEL_PASS", "SERVER_IP", "VLESS_PBK", "PAY_TOKEN"]
        for var in required:
            if not os.getenv(var):
                logging.critical(f"💥 Критическая ошибка: переменная {var} не задана! Бот не запустится.")
                sys.exit(1)

        if ADMIN_ID <= 0:
            logging.critical("💥 Критическая ошибка: ADMIN_ID должен быть больше нуля!")
            sys.exit(1)
        
        if not VLESS_SID:
            logging.warning("⚠️ VLESS_SID не задан. Если REALITY требует SID, ключи могут не работать.")
        
        init_db()
        bot = Bot(token=BOT_TOKEN)
        scheduler = AsyncIOScheduler()
        scheduler.add_job(check_expirations, "interval", minutes=10, args=[bot])
        scheduler.start()

        startup_check_task = asyncio.create_task(check_expirations(bot))
        billing_engine.start()
        

        logging.info("🚀 MetronVPN запущен и готов к работе.")
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        finally:
            billing_engine.stop()
            await bot.session.close()
            if panel_client.panel_session and not panel_client.panel_session.closed:
                await panel_client.panel_session.close()
            if scheduler.running:
                scheduler.shutdown(wait=False)
            if startup_check_task and not startup_check_task.done():
                startup_check_task.cancel()

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("👋 Работа бота завершена.")