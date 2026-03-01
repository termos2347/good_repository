import os
import json
import asyncio
import logging
import signal
import aiohttp
import traceback
import platform
import time
from dotenv import load_dotenv
from config import app_config as config
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update, Message, ErrorEvent
from aiogram.exceptions import TelegramAPIError
from config import Config
from bot_controller import BotController
from pathlib import Path
from state_manager import StateManager
from rss_parser import AsyncRSSParser
from image_generator import AsyncImageGenerator
from ai import AsyncAI
from telegram_interface import AsyncTelegramBot
from visual_interface import UIBuilder
from typing import Optional, Dict, Any, Union
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

logger = logging.getLogger('AsyncMain')
load_dotenv()

class TelegramLogHandler(logging.Handler):
    """Кастомный обработчик логов для отправки в Telegram"""
    def __init__(self, bot, owner_id, notify_level=logging.ERROR):
        super().__init__()
        self.bot = bot
        self.owner_id = owner_id
        self.notify_level = notify_level
        self.rate_limit = 60  # секунды между сообщениямиsrthefth
        self.last_sent = 0

    def emit(self, record):
        try:
            # Проверяем уровень важности
            if record.levelno < self.notify_level:
                return
                
            # Проверка rate limit
            current_time = time.time()
            if current_time - self.last_sent < self.rate_limit:
                return
                
            message = self.format(record)
            asyncio.create_task(self.send_telegram(message))
            self.last_sent = current_time
        except Exception as e:
            print(f"Ошибка в обработчике логов: {str(e)}")

    async def send_telegram(self, message):
        """Асинхронная отправка сообщения в Telegram"""
        try:
            # Отправляем полное сообщение без обрезки
            await self.bot.send_message(
                chat_id=self.owner_id,
                text=f"<b>⚠️ БОТ: {logging.getLevelName(self.notify_level)}</b>\n\n<code>{message}</code>",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Ошибка отправки в Telegram: {str(e)}")

async def shutdown(controller, connector, session):
    """Корректное завершение работы"""
    logger.info("Shutting down...")
    try:
        if controller:
            await controller.stop()
    except Exception as e:
        logger.error(f"Controller shutdown error: {str(e)}")
    
    if session and not session.closed:
        try:
            await session.close()
            logger.info("aiohttp session closed")
        except Exception as e:
            logger.error(f"Error closing session: {str(e)}")
    
    if connector:
        try:
            await connector.close()
            logger.info("TCP connector closed")
        except Exception as e:
            logger.error(f"Error closing connector: {str(e)}")
    
    # Отменяем все оставшиеся задачи
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    
    # Ожидаем завершения задач
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

def setup_logging(debug_mode: bool = False) -> None:
    """Настройка логирования"""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    level = logging.DEBUG if debug_mode else logging.INFO
    
    # Основной лог (ротация по размеру)
    file_handler = RotatingFileHandler(
        filename=os.path.join(log_dir, 'rss_bot.log'),
        maxBytes=10*1024*1024,  # 10 MB
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    
    # Лог ошибок (ротация по дням)
    error_handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, 'errors.log'),
        when='midnight',
        backupCount=7,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(logging.Formatter(log_format))
    
    # Консольный вывод
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    
    # Настройка корневого логгера
    logging.basicConfig(
        level=level,
        format=log_format,
        handlers=[file_handler, error_handler, console_handler]
    )
    
    # Уменьшаем уровень логирования для шумных библиотек
    for lib in ['asyncio', 'aiohttp', 'PIL']:
        logging.getLogger(lib).setLevel(logging.WARNING)

async def test_bot_commands(telegram_bot: AsyncTelegramBot, owner_id: int):
    """Проверка возможности отправки сообщений"""
    try:
        await telegram_bot.bot.send_message(  # Используем telegram_bot вместо bot
            chat_id=owner_id,
            text="🤖 Бот успешно запущен!",
            parse_mode="HTML"
        )
        return True
    except Exception as e:
        logger.error(f"Test message failed: {str(e)}")
        return False

async def run_bot():
    logger.info("===== ASYNC BOT STARTING =====")
    
    # Инициализация конфигурации
    config = Config()
    setup_logging(config.DEBUG_MODE)
    
    # Проверка обязательных параметров
    if not config.TOKEN:
        logger.critical("TELEGRAM_TOKEN is required but not set")
        return
        
    if not config.CHANNEL_ID:
        logger.critical("CHANNEL_ID is required but not set")
        return
    
    # Инициализация StateManager
    state_manager = StateManager(config.STATE_FILE, config.MAX_ENTRIES_HISTORY, config)
    logger.info("State manager initialized")
    
    # Создаем TCP коннектор для aiohttp
    connector = aiohttp.TCPConnector(
        force_close=True,
        enable_cleanup_closed=True,
        limit=0
    )
    
    # Инициализируем переменные для управления ресурсами
    session: Optional[aiohttp.ClientSession] = None
    telegram_bot: Optional[AsyncTelegramBot] = None
    controller: Optional[BotController] = None
    polling_task: Optional[asyncio.Task] = None
    tg_handler = None
    
    try:
        # Создаем aiohttp сессию
        session = aiohttp.ClientSession(connector=connector)
        logger.info("Created aiohttp session")
        
        # Инициализация Telegram бота
        telegram_bot = AsyncTelegramBot(
            token=config.TOKEN,
            channel_id=config.CHANNEL_ID,
            config=config
        )
        logger.info("Telegram bot initialized")

        # Блокировка не-владельцев
        @telegram_bot.dp.message()
        async def global_blocker(message: Message):
            if message.from_user.id != config.OWNER_ID:
                await message.answer("⛔ Доступ запрещен")
                return
        
        # Установка меню команд
        await telegram_bot.setup_commands()
        logger.info("Telegram commands menu initialized")
        
        # Проверка возможности отправки сообщений
        if config.OWNER_ID and not await test_bot_commands(telegram_bot, config.OWNER_ID):
            logger.error("Bot can't send messages, check TOKEN and OWNER_ID")
        
        # Инициализация компонентов
        # Создаем временный экземпляр RSS-парсера без контроллера
        rss_parser = AsyncRSSParser(session, config.PROXY_URL)
        
        # Инициализация AI и генератора изображений
        AI = AsyncAI(config, session)
        image_generator = AsyncImageGenerator(config)
        logger.info("All components initialized")
        
        # Создание контроллера
        controller = BotController(
            config=config,
            state_manager=state_manager,
            rss_parser=rss_parser,
            image_generator=image_generator,
            AI=AI,
            telegram_bot=telegram_bot
        )
        logger.info("Bot controller created")
        
        # Связываем контроллер с конфигом
        config.controller = controller
        logger.info("Controller successfully set in config")
        
        # Обновляем RSS-парсер с контроллером и callback
        rss_parser.set_controller(controller)
        rss_parser.set_on_session_recreate(controller._recreate_session)
        
        # Передаем контроллер в Telegram бота
        telegram_bot.set_controller(controller)
        logger.info("Controller linked to Telegram bot")
        
        # Добавляем обработчик логов для отправки ошибок в Telegram
        if config.OWNER_ID:
            # Определяем уровень уведомлений из конфига
            notify_level = getattr(logging, config.NOTIFY_LEVEL, logging.ERROR)
            tg_handler = TelegramLogHandler(
                bot=telegram_bot.bot,
                owner_id=config.OWNER_ID,
                notify_level=notify_level
            )
            tg_handler.setFormatter(logging.Formatter('%(name)s - %(levelname)s - %(message)s'))
            logging.getLogger().addHandler(tg_handler)
            logger.info("Telegram error handler initialized")
        
        # Запуск контроллера
        if not await controller.start():
            raise RuntimeError("Failed to start bot controller")
        logger.info("RSS processing task started")
        
        # Инициализация диспетчера для обработки команд Telegram
        dp = telegram_bot.dp
        
        # Обработчик ошибок
        @dp.errors()
        async def errors_handler(event: ErrorEvent):
            logger.error(f"Update {event.update} caused error: {event.exception}")
            return True
        
        # Запуск обработки команд Telegram
        if telegram_bot and telegram_bot.bot:
            polling_task = asyncio.create_task(
                dp.start_polling(
                    telegram_bot.bot,
                    allowed_updates=dp.resolve_used_update_types()
                ),
                name="telegram_polling"
            )
            logger.info("Telegram polling task started")
        else:
            logger.warning("Skipping Telegram polling setup - bot not available")
        
        # Для Windows используем альтернативную обработку Ctrl+C
        if platform.system() == 'Windows':
            logger.info("Windows detected, using alternative signal handling")
            # Создаем событие для отслеживания завершения
            shutdown_event = asyncio.Event()
            
            # Задача для отслеживания Ctrl+C
            async def windows_shutdown_handler():
                try:
                    while True:
                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    logger.info("Ctrl+C received, shutting down")
                    shutdown_event.set()
            
            shutdown_task = asyncio.create_task(windows_shutdown_handler())
        else:
            # Для Unix-систем используем стандартные сигналы
            loop = asyncio.get_running_loop()
            shutdown_event = asyncio.Event()
            
            for s in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    s, 
                    lambda: shutdown_event.set()
                )
        
        logger.info("Bot started successfully. Press Ctrl+C to stop.")
        logger.info("""          
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⣿⡟⡿⢯⡻⡝⢯⡝⡾⣽⣻⣟⡿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⢻⠳⣏⠧⠹⡘⢣⠑⠎⠱⣈⠳⢡⠳⢭⣛⠷⣟⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⣝⣎⢣⠡⠌⣢⠧⠒⠃⠁⠈⠉⠉⠑⠓⠚⠦⣍⡞⡽⣞⡿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⣝⠮⡜⣢⠕⠊⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠐⠄⡙⠲⣝⡺⡽⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⣿⣿⡹⢎⣳⠞⡡⠊⠀⠀⠀⣀⣤⣤⣶⣶⣤⣤⣀⡈⠂⠄⠙⠱⡌⠳⣹⢎⡿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⣽⣟⣳⡝⡼⢁⠎⠀⡀⢁⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⡄⠰⣄⠈⠓⢌⠛⢽⣣⡟⢿⠿⣿⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣟⡿⣽⠳⡼⢁⡞⠀⡜⢰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡆⢸⢵⠀⠀⠁⠂⠤⣉⠉⠓⠒⠚⠦⠥⡈⠉⣙⢛⡿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣟⡾⣽⣏⢳⢃⣞⠃⡼⢀⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡄⠀⠀⠀⠀⠀⠀⠀⠀⠁⢀⣀⠤⠐⢋⡰⣌⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣟⣮⢳⣿⠶⠁⠖⠃⠀⠁⢸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠿⠿⠟⠛⠛⠀⠀⠀⠀⢀⡤⠤⠐⠒⣉⠡⣄⠶⣭⣿⣽⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⡿⠿⢉⡢⠝⠁⠀⠃⠀⠀⠀⠀⠀⠿⠃⠿⠿⠿⠛⠋⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠀⣀⢤⣰⣲⣽⣾⡟⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣟⡿⡚⠏⠁⠀⠐⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⠂⣠⠀⣯⣗⣮⢿⣷⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⢯⡝⠠⠁⠀⠀⠠⠤⠀⠀⠀⠀⡀⠢⣄⣀⡀⠐⠤⡀⠀⠀⠀⢤⣄⣀⠤⣄⣤⢤⣖⡾⠋⢁⡼⠁⣸⡿⣞⣽⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣷⣾⣵⣦⣶⣖⣳⣶⣝⣶⣯⣷⣽⣷⣾⣶⣽⣯⣶⠄⠈⠒⣤⣀⠉⠙⠛⠛⠋⠋⢁⣠⠔⠁⠀⢰⣿⣽⣯⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣦⡄⡀⡉⠛⠓⠶⠶⠒⠛⠋⠀⠀⢀⣼⣻⢷⣾⣷⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣾⣧⡵⣌⣒⢂⠀⣀⣀⣠⣤⣶⣿⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣿⣾⣷⣯⣿⣧⣿⣷⣿⣷⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
""")
        # Основной цикл ожидания
        try:
            await shutdown_event.wait()
            logger.info("Shutdown event triggered")
        except asyncio.CancelledError:
            logger.info("Main task cancelled")
            
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.critical(f"Fatal error in main loop: {str(e)}\n{traceback.format_exc()}")
        # Отправляем критическую ошибку владельцу
        if config.OWNER_ID and telegram_bot:
            try:
                await telegram_bot.bot.send_message(
                    chat_id=config.OWNER_ID,
                    text=f"💥 КРИТИЧЕСКАЯ ОШИБКА\n\n{str(e)}",
                    parse_mode="HTML"
                )
            except Exception as te:
                logger.error(f"Failed to send error message: {str(te)}")
    finally:
        logger.info("===== SHUTDOWN SEQUENCE STARTED =====")
        
        # Удаляем обработчик Telegram из логгера
        if tg_handler:
            logging.getLogger().removeHandler(tg_handler)
            logger.info("Telegram log handler removed")
        
        try:
            # Выполняем асинхронную очистку ресурсов
            await shutdown(controller, connector, session)
        except Exception as e:
            logger.error(f"Error during shutdown: {str(e)}")
        
        # Отмена задачи опроса Telegram
        if polling_task and not polling_task.done():
            try:
                polling_task.cancel()
                logger.info("Telegram polling task cancellation requested")
            except Exception as e:
                logger.error(f"Error cancelling polling task: {str(e)}")
        
        # Закрытие Telegram бота
        if telegram_bot:
            try:
                await telegram_bot.close()
                logger.info("Telegram bot closed")
            except Exception as e:
                logger.error(f"Error closing Telegram bot: {str(e)}")
        
        # Отменяем все оставшиеся задачи
        current_task = asyncio.current_task()
        tasks = [t for t in asyncio.all_tasks() if t is not current_task and not t.done()]
        
        if tasks:
            logger.info(f"Cancelling {len(tasks)} pending tasks")
            for task in tasks:
                try:
                    task.cancel()
                except Exception as e:
                    logger.error(f"Error cancelling task: {str(e)}")
            
            # Ожидаем завершения задач с обработкой исключений
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f"Error gathering tasks: {str(e)}")
            logger.info("All pending tasks cancelled")
        
        logger.info("===== ASYNC BOT STOPPED =====")

async def check_internet_connection(session):
    while True:
        try:
            async with session.get("https://google.com", timeout=10) as resp:
                if resp.status != 200:
                    logger.warning("Интернет соединение нестабильно")
        except Exception:
            logger.error("Нет интернет соединения!")
        await asyncio.sleep(60)
        
if __name__ == "__main__":
    # Создаем новый цикл событий
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Запускаем основную корутину
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.critical(f"Top-level error: {str(e)}\n{traceback.format_exc()}")
    finally:
        try:
            # Останавливаем и закрываем цикл только если он не закрыт
            if not loop.is_closed():
                # Собираем оставшиеся задачи
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                
                # Отменяем все задачи
                for task in pending:
                    task.cancel()
                
                # Запускаем цикл для обработки отмены
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                
                # Останавливаем и закрываем цикл
                loop.stop()
                loop.close()
            logger.info("Event loop stopped and closed")
        except Exception as e:
            logger.error(f"Error during final cleanup: {str(e)}")