import asyncio
from collections import deque
import json
import os
import logging
import re
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from state_manager import StateManager
from typing import Optional, List, Dict, Any, Union
from aiogram import Bot, Dispatcher
from aiogram.types import Message, BotCommand, InputFile, FSInputFile, MenuButtonCommands, CallbackQuery, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import MenuButtonType
from aiogram.filters import Command
from config import Config
from bot_controller import BotController
from visual_interface import UIBuilder
from aiogram.types import BufferedInputFile
from aiogram.types import Message as TelegramMessage
from aiogram.exceptions import TelegramBadRequest


logger = logging.getLogger('AsyncTelegramBot')

class InputValidator:
    """Класс для валидации вводимых пользователем значений"""
    @staticmethod
    def validate_temperature(text: str) -> float:
        """Валидация температуры ИИ (0.1-1.0)"""
        if not text.replace('.', '', 1).isdigit():
            raise ValueError("Требуется числовое значение")
            
        value = float(text)
        if value < 0.1 or value > 1.0:
            raise ValueError("Допустимый диапазон: 0.1-1.0")
            
        return round(value, 1)  # Округление до 1 знака

    @staticmethod
    def validate_tokens(text: str) -> int:
        """Валидация количества токенов (500-10000)"""
        try:
            # Поддержка экспоненциальной записи (1e3)
            value = float(text)
            value = int(value)
        except ValueError:
            raise ValueError("Требуется целое число")
        
        if value < 500 or value > 10000:
            raise ValueError("Допустимый диапазон: 500-10000")
            
        return value

    @staticmethod
    def validate_interval(text: str) -> int:
        """Валидация интервалов времени с поддержкой единиц измерения"""
        multipliers = {'s': 1, 'm': 60, 'h': 3600}
        unit = text[-1].lower() if text else ''
        
        try:
            if unit in multipliers:
                num = float(text[:-1])
                value = num * multipliers[unit]
            else:
                value = float(text)
                
            # Ограничения: 60 сек - 24 часа
            value = max(60, min(86400, value))
            return int(value)
        except ValueError:
            raise ValueError("Формат: число[ед] (например: 5m, 300, 0.5h)")

    @staticmethod
    def validate_boolean(text: str) -> bool:
        """Валидация булевых значений"""
        true_values = ['true', '1', 'yes', 'y', 'on', 'вкл', 'да']
        false_values = ['false', '0', 'no', 'n', 'off', 'выкл', 'нет']
        
        clean_text = text.strip().lower()
        if clean_text in true_values:
            return True
        if clean_text in false_values:
            return False
            
        raise ValueError("Используйте: да/нет, вкл/выкл, true/false")

    @staticmethod
    def validate_integer(text: str, min_val: int, max_val: int) -> int:
        """Общая валидация целых чисел"""
        try:
            value = int(text)
        except ValueError:
            raise ValueError("Требуется целое число")
            
        if value < min_val or value > max_val:
            raise ValueError(f"Допустимый диапазон: {min_val}-{max_val}")
            
        return value

    @staticmethod
    def validate_schedule(text: str) -> List[str]:
        """Валидация формата расписания"""
        times = []
        errors = []
        
        for part in text.split(','):
            part = part.strip()
            if not part:
                continue
                
            # Проверка формата ЧЧ:ММ
            if re.match(r'^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$', part):
                times.append(part)
            else:
                errors.append(part)
        
        if not times:
            raise ValueError(
                "Неверный формат расписания\n"
                "Используйте: ЧЧ:ММ,ЧЧ:ММ,... (например: 9:30,12:00,18:45)"
            )
        
        if errors:
            raise ValueError(
                "Некорректные значения времени: " + ", ".join(errors) + "\n"
                "Формат: ЧЧ:ММ (например: 9:30 или 09:30)"
            )
            
        # Ограничение количества точек
        if len(times) > 24:
            raise ValueError("Максимум 24 временных точки")
            
        return times

class AsyncTelegramBot:
    def __init__(self, token: str, channel_id: str, config: Config):
        self.token = token
        self.channel_id = channel_id
        self.config = config
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.controller: Optional[BotController] = None
        self.ui = UIBuilder(config)
        self.pending_input = {}  # user_id: {'param': param_name, 'type': 'general'}
        self.pending_input_timeouts = {}
        self.pending_input_retries = {}
        self.validator = InputValidator()

        # Запуск фоновой задачи очистки
        self.cleanup_task = asyncio.create_task(self._cleanup_pending_inputs())
        self.dp.message.register(self.handle_set_schedule, Command("set_schedule"))
        self._register_handlers()
    
    def set_controller(self, controller):
        """Устанавливает контроллер для обработки команд"""
        self.controller = controller
        
    async def setup_commands(self) -> None:
        """Устанавливает меню команд в строке ввода"""
        commands = [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="menu", description="Открыть панель управления"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="status", description="Статус бота"),
            BotCommand(command="stats", description="Статистика"),
            BotCommand(command="rss_list", description="Список RSS-лент"),
            BotCommand(command="rss_add", description="Добавить RSS"),
            BotCommand(command="rss_remove", description="Удалить RSS"),
            BotCommand(command="pause", description="Приостановить"),
            BotCommand(command="resume", description="Возобновить"),
            BotCommand(command="settings", description="Текущие настройки"),
            BotCommand(command="set", description="Изменить параметр"),
            BotCommand(command="clear_history", description="Очистить историю постов"),
            BotCommand(command="params_list", description="Список всех параметров"),
            BotCommand(command="param_info", description="Информация о параметре"),
            BotCommand(command="set_all", description="Изменить любой параметр"),
        ]
        await self.bot.set_my_commands(commands)
        await self.bot.set_chat_menu_button(menu_button=MenuButtonCommands(type=MenuButtonType.COMMANDS))
    
    async def send_post(
        self,
        title: str,
        description: str,
        link: str,
        image_path: Optional[str] = None
    ) -> bool:
        """Отправляет пост в Telegram канал"""
        try:
            post_text = f"<b>{title}</b>\n\n{description}\n\n<a href='{link}'>Читать далее</a>"
            
            if image_path:
                if not os.path.exists(image_path):
                    logger.error(f"Изображение не найдено: {image_path}")
                    return False
                    
                photo = FSInputFile(image_path)
                await self.bot.send_photo(
                    chat_id=self.channel_id,
                    photo=photo,
                    caption=post_text,
                    parse_mode="HTML"
                )
                logger.info(f"Отправлен пост с изображением: {title[:50]}...")
            else:
                await self.bot.send_message(
                    chat_id=self.channel_id,
                    text=post_text,
                    parse_mode="HTML"
                )
                logger.info(f"Отправлен текстовый пост: {title[:50]}...")
                
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки поста '{title[:30]}...': {str(e)}")
            return False
    
    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = "HTML",
        **kwargs
    ) -> bool:
        """Отправляет текстовое сообщение в указанный чат"""
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                **kwargs
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {str(e)}")
            return False
        
    def _register_handlers(self) -> None:
        self.dp.message.register(self.handle_start, Command("start", "help", "menu"))
        self.dp.message.register(self.handle_status, Command("status"))
        self.dp.message.register(self.handle_stats, Command("stats"))
        self.dp.message.register(self.handle_rss_list, Command("rss_list"))
        self.dp.message.register(self.handle_rss_add, Command("rss_add"))
        self.dp.message.register(self.handle_rss_remove, Command("rss_remove"))
        self.dp.message.register(self.handle_pause, Command("pause"))
        self.dp.message.register(self.handle_resume, Command("resume"))
        self.dp.message.register(self.handle_settings, Command("settings"))
        self.dp.message.register(self.handle_set, Command("set"))
        self.dp.message.register(self.handle_clear_history, Command("clear_history"))
        self.dp.message.register(self.handle_params_list, Command("params_list"))
        self.dp.message.register(self.handle_param_info, Command("param_info"))
        self.dp.message.register(self.handle_set_all, Command("set_all"))
        self.dp.message.register(self.handle_message)
        self.dp.message.register(self.handle_set_schedule, Command("set_schedule"))
        self.dp.message.register(self.handle_set_mode, Command('set_mode'))
        
        # Привязка команд к callback-кнопкам
        self.dp.callback_query.register(self.handle_resume_cmd, lambda c: c.data == "resume_cmd")
        self.dp.callback_query.register(self.handle_pause_cmd, lambda c: c.data == "pause_cmd")
        
        self.dp.callback_query.register(self.handle_callback)
        self.dp.callback_query.register(self.show_publication_settings, lambda c: c.data == "settings_publication")
        self.dp.callback_query.register(self.toggle_publication_mode, lambda c: c.data.startswith("set_pub_mode_"))
        self.dp.callback_query.register(self.handle_edit_schedule, lambda c: c.data == "edit_schedule")
        self.dp.callback_query.register(self.handle_edit_delay, lambda c: c.data == "edit_delay")
        self.dp.callback_query.register(self.toggle_publication_mode, lambda c: c.data.startswith("toggle_pub_mode_"))
        self.dp.callback_query.register(self.show_publication_settings_menu, lambda c: c.data == "publication_settings")
        self.dp.callback_query.register(self.handle_manage_schedule, lambda c: c.data == "manage_schedule")
        self.dp.callback_query.register(self.handle_show_schedule, lambda c: c.data == "show_schedule")
        self.dp.callback_query.register(self.handle_switch_publication_mode, lambda c: c.data == "switch_publication_mode")
        self.dp.callback_query.register(self.handle_set_publication_mode, lambda c: c.data.startswith("set_mode_"))

    async def handle_callback(self, callback: CallbackQuery) -> None:
        """Основной обработчик callback'ов"""
        try:
            if not callback.message or not isinstance(callback.message, TelegramMessage):
                await callback.answer("Ошибка сообщения")
                return

            user_id = callback.from_user.id
            chat_id = callback.message.chat.id
            data = callback.data

            logger.debug(f"Callback от пользователя {user_id}: {data}")
            
            if data == "main_menu":
                await self.send_main_menu(user_id, chat_id)
            elif data == "main" or data == "main_menu":
                await self.show_main_menu(callback)
            elif data == "stats":
                await self.show_statistics(callback)
            elif data == "monitoring":
                await self.show_monitoring(callback)
            elif data == "settings":
                await self.show_settings_menu(callback)
            elif data == "settings_general":
                await self.show_general_settings(callback)
            elif data == "settings_images":
                await self.show_image_settings(callback)
            elif data == "settings_ai":
                await self.show_ai_settings(callback)
            elif data == "rss_list":
                await self.handle_rss_list(callback)
            elif data == "settings_rss":
                await self.show_rss_settings(callback)
            elif data == "settings_notify":
                await self.show_notify_settings(callback)
            elif data == "change_theme":
                await self.show_theme_selector(callback)
            elif data.startswith("set_theme_"):
                await self.set_theme(callback)
            elif data == "start_bot":
                await self.handle_start_bot(callback)
            elif data == "stop_bot":
                await self.handle_stop_bot(callback)
            elif data == "back_to_settings":
                await self.show_settings_menu(callback)
            
            # Основные настройки
            elif data == "settings_general":
                await self.show_general_settings(callback)
            elif data == "edit_general_settings":
                await self.edit_general_settings(callback)
            elif data.startswith("edit_general_"):
                await self.edit_general_param(callback)
            elif data.startswith("set_general_"):
                await self.set_general_param(callback)
            elif data == "save_general_settings":
                await self.save_general_settings(callback)
            elif data == "cancel_general_edit":
                await self.cancel_general_edit(callback)

            # AI настройки
            elif data == "settings_ai":
                await self.show_ai_settings(callback)
            elif data == "edit_ai_settings":
                await self.edit_ai_settings(callback)
            elif data == "save_ai_settings":
                await self.save_ai_settings(callback)
            elif data == "cancel_ai_edit":
                await self.cancel_ai_edit(callback)
            elif data.startswith("edit_ai_"):  # edit_ai_model, edit_ai_temp, edit_ai_tokens
                await self.edit_ai_param(callback)
            elif data == "toggle_ai_enabled":
                await self.toggle_ai_enabled(callback)
            elif data.startswith("set_ai_model:"):
                await self.set_ai_model(callback)
            elif data.startswith("set_ai_temp:"):
                await self.set_ai_temp(callback)
            elif data == "set_ai_temp_custom":
                await self.set_ai_temp_custom(callback)
            elif data.startswith("set_ai_tokens:"):
                await self.set_ai_tokens(callback)
            elif data == "set_ai_tokens_custom":
                await self.set_ai_tokens_custom(callback)
            
            # RSS настройки
            elif data == "rss_settings":
                await self.show_rss_settings(callback)
            elif data == "edit_rss_settings":
                await self.show_rss_settings(callback, edit_mode=True)
            elif data == "save_rss_settings":
                await callback.answer("Настройки RSS сохранены")
                await self.show_rss_settings(callback)
            elif data == "rss_add_start":
                await self.start_rss_add(callback)
            elif data == "rss_remove_start":
                await self.start_rss_remove(callback)
            elif data.startswith("rss_remove_"):
                await self.confirm_rss_remove(callback)
            elif data.startswith("rss_toggle_"):
                await self.toggle_rss_feed(callback)
            elif data == "rss_refresh":
                if hasattr(self, 'refresh_rss_status'):
                    await self.refresh_rss_status(callback)
                else:
                    logger.error("refresh_rss_status method missing")
                    await callback.answer("Функция недоступна")
            
            # Обработка повторного ввода и отмены
            elif data.startswith("retry_"):
                await self.handle_retry_input(callback)
            elif data.startswith("cancel_edit_"):
                await self.handle_cancel_edit(callback)

            else:
                logger.warning(f"Неизвестный callback: {data}")
                await callback.answer("Функция в разработке")

            await callback.answer()
        except Exception as e:
            logger.error(f"Ошибка обработки callback: {str(e)}", exc_info=True)
            await callback.answer("Ошибка обработки запроса")

    async def _cleanup_pending_inputs(self):
        """Очистка просроченных ожиданий ввода"""
        while True:
            current_time = time.time()
            expired_users = [
                user_id for user_id, timeout in self.pending_input_timeouts.items()
                if timeout < current_time
            ]
            
            for user_id in expired_users:
                if user_id in self.pending_input:
                    try:
                        await self.bot.send_message(
                            chat_id=self.pending_input[user_id]['chat_id'],
                            text="⏱️ Время ввода истекло. Операция отменена."
                        )
                    except:
                        pass
                    del self.pending_input[user_id]
                if user_id in self.pending_input_timeouts:
                    del self.pending_input_timeouts[user_id]
                if user_id in self.pending_input_retries:
                    del self.pending_input_retries[user_id]
            
            await asyncio.sleep(60)  # Проверка каждую минуту

    async def show_monitoring(self, callback: CallbackQuery) -> None:
        """Показывает панель мониторинга"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        stats = self.controller.get_status_text()
        await self.bot.send_message(
            chat_id=callback.message.chat.id,
            text=stats,
            parse_mode="HTML"
        )

    async def set_theme(self, callback: CallbackQuery) -> None:
        """Устанавливает тему оформления"""
        theme_name = callback.data.replace("set_theme_", "")
        if theme_name in self.ui.THEMES:
            self.ui.user_themes[callback.from_user.id] = self.ui.THEMES[theme_name]
            await callback.answer(f"Тема изменена на {theme_name}")
            await self.show_settings_menu(callback)
        else:
            await callback.answer("Неизвестная тема")

    async def show_general_settings(self, callback: CallbackQuery) -> None:
        """Показывает общие настройки"""
        text = (
            "⚙️ <b>Общие настройки</b>\n\n"
            f"• Интервал проверки: {self.config.CHECK_INTERVAL} сек\n"
            f"• Макс. постов за цикл: {self.config.MAX_POSTS_PER_CYCLE}\n"
            f"• Постов в час: {self.config.POSTS_PER_HOUR}\n"
            f"• Мин. задержка между постами: {self.config.MIN_DELAY_BETWEEN_POSTS} сек"
        )
        
        keyboard = await self.ui.back_to_settings()
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    async def show_ai_settings(self, target: Union[Message, CallbackQuery], edit_mode: bool = False) -> None:
        """Универсальный метод для отображения настроек AI"""
        user_id = target.from_user.id
        text, keyboard = await self.ui.ai_settings_view(user_id, edit_mode)
        
        if isinstance(target, CallbackQuery):
            try:
                await target.message.edit_text(
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except Exception:
                await self.bot.send_message(
                    chat_id=target.message.chat.id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
        else:  # Это объект Message
            await target.answer(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )

    async def show_general_settings(self, target: Union[Message, CallbackQuery], edit_mode: bool = False) -> None:
        """Универсальный метод для отображения общих настроек"""
        user_id = target.from_user.id
        text, keyboard = await self.ui.general_settings_view(user_id, edit_mode)
        
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=keyboard)
        else:  # Это объект Message
            await target.answer(text, reply_markup=keyboard)
    
    async def edit_general_settings(self, callback: CallbackQuery):
        """Вход в режим редактирования"""
        await self.ui.start_general_edit(callback.from_user.id)
        await self.show_general_settings(callback, edit_mode=True)
    
    async def edit_general_param(self, callback: CallbackQuery):
        """Обработка выбора параметра"""
        param = callback.data.replace("edit_general_", "")
        keyboard = await self.ui.general_param_selector(callback.from_user.id, param)
        await callback.message.edit_text(f"Выберите значение для {param}:", reply_markup=keyboard)
    
    async def set_general_param(self, callback: CallbackQuery) -> None:
        """Обработчик установки значений для основных настроек"""
        # Извлекаем данные после префикса "set_general_"
        data_str = callback.data.replace("set_general_", "", 1)
        user_id = callback.from_user.id
        
        # Обработка ручного ввода (кнопка "Вручную")
        if data_str.endswith("_custom"):
            param = data_str.replace("_custom", "")
            
            # Сохраняем информацию о параметре
            self.pending_input[user_id] = {
                'param': param,
                'type': 'general',
                'chat_id': callback.message.chat.id,
            }
            
            # Устанавливаем таймаут 5 минут
            self.pending_input_timeouts[user_id] = time.time() + 300
            
            # Отправляем запрос с примерами
            examples = {
                'temperature': "0.1-1.0 (например: 0.7)",
                'max_tokens': "500-10000 (например: 2500)",
                'check_interval': "60-86400 сек (например: 300 или 5m)",
                'min_delay_between_posts': "10-3600 сек (например: 60)",
                'posts_per_hour': "1-100 (например: 10)"
            }.get(param, "числовое значение")
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit_general")]
                ]
            ])
            
            await callback.message.answer(
                f"✏️ Введите новое значение для параметра '{param}':\n(Формат: {examples})",
                reply_markup=keyboard
            )
            await callback.answer()
            return
        
        # Обработка предустановленных значений (обычный выбор)
        if ":" not in data_str:
            logger.error(f"Invalid callback data format: {callback.data}")
            await callback.answer("Ошибка формата данных")
            return
            
        param, value_str = data_str.split(":", 1)
        
        try:
            # Преобразуем значение в число (целое или дробное)
            value = float(value_str) if "." in value_str else int(value_str)
            
            # Обновляем временное значение в UI
            await self.ui.update_general_setting(
                callback.from_user.id,
                param,
                value
            )
            
            # Обновляем сообщение с настройками
            await self.show_general_settings(callback, edit_mode=True)
            await callback.answer(f"✅ Значение обновлено: {value}")
        except ValueError:
            logger.error(f"Invalid value for parameter {param}: {value_str}")
            await callback.answer(f"❌ Недопустимое значение: {value_str}")
    
    async def save_general_settings(self, callback: CallbackQuery):
        """Сохранение изменений"""
        try:
            changes = await self.ui.save_general_settings(callback.from_user.id)
            if not changes:
                await callback.answer("Настройки не изменены")
                return
            
            # Применение изменений в конфигурации
            for param, value in changes.items():
                self.config.update_param(param, value)
            
            # Формирование отчета
            changes_text = "\n".join([f"• {param}: {value}" for param, value in changes.items()])
            text = f"✅ Основные настройки обновлены:\n\n{changes_text}"
            
            await callback.message.edit_text(text)
            await asyncio.sleep(3)
            await self.show_general_settings(callback)
            
        except Exception as e:
            logger.error(f"Ошибка сохранения: {str(e)}")
            await callback.answer("Ошибка сохранения настроек", show_alert=True)

    async def show_publication_settings(
        self, 
        target: Union[Message, CallbackQuery], 
        edit_mode: bool = False
    ) -> None:
        """Показывает объединенные настройки публикации и основные"""
        if not self.controller:
            await target.answer("Контроллер не подключен")
            return
        
        user_id = target.from_user.id
        config = self.controller.config
        controller = self.controller
        
        # Получаем текущий режим публикации
        pub_mode = controller.publication_mode
        pub_mode_text = "Расписание" if pub_mode == 'schedule' else "Задержка"
        
        # Формируем текст в зависимости от режима
        if pub_mode == 'schedule':
            schedule_times = ", ".join(
                [t.strftime("%H:%M") for t in controller.publication_schedule]
            )
            settings_text = (
                f"⏰ <b>Режим публикации:</b> {pub_mode_text}\n"
                f"<b>Расписание:</b> {schedule_times}\n"
            )
        else:
            settings_text = (
                f"⏰ <b>Режим публикации:</b> {pub_mode_text}\n"
                f"<b>Мин. задержка:</b> {config.MIN_DELAY_BETWEEN_POSTS} сек\n"
            )
        
        # Добавляем основные настройки
        settings_text += (
            f"\n⚙️ <b>Основные настройки</b>\n"
            f"• Интервал проверки: {config.CHECK_INTERVAL} сек\n"
            f"• Макс. постов за цикл: {config.MAX_POSTS_PER_CYCLE}\n"
            f"• Постов в час: {config.POSTS_PER_HOUR}\n"
        )
        
        # Создаем клавиатуру
        builder = InlineKeyboardBuilder()
        
        # Кнопка смены режима публикации
        new_mode = 'schedule' if pub_mode == 'delay' else 'delay'
        new_mode_text = "Расписание" if new_mode == 'schedule' else "Задержка"
        builder.button(
            text=f"🔄 Сменить на {new_mode_text}", 
            callback_data=f"toggle_pub_mode_{new_mode}"
        )
        
        # Кнопки редактирования параметров в зависимости от режима
        if pub_mode == 'schedule':
            builder.button(
                text="✏️ Изменить расписание", 
                callback_data="edit_schedule"
            )
        else:
            builder.button(
                text="✏️ Изменить задержку", 
                callback_data="edit_delay"
            )
        
        # Другие основные настройки
        builder.button(
            text="⚙️ Интервал проверки", 
            callback_data="edit_general_check_interval"
        )
        builder.button(
            text="📊 Макс. постов/цикл", 
            callback_data="edit_general_max_posts_per_cycle"
        )
        builder.button(
            text="🚀 Постов в час", 
            callback_data="edit_general_posts_per_hour"
        )
        
        builder.adjust(1)  # По одной кнопке в строке
        builder.row(*[
            InlineKeyboardButton(text="◀️ Назад", callback_data="settings"),
            InlineKeyboardButton(text="💾 Сохранить", callback_data="save_general_settings")
        ])
        
        keyboard = builder.as_markup()
        
        # Отправка/редактирование сообщения
        if isinstance(target, CallbackQuery):
            try:
                await target.message.edit_text(
                    text=settings_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except Exception:
                await self.bot.send_message(
                    chat_id=target.message.chat.id,
                    text=settings_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
        else:
            await target.answer(
                text=settings_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    async def toggle_publication_mode(self, callback: CallbackQuery) -> None:
        """Переключает режим публикации между задержкой и расписанием"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
        
        # Извлекаем новый режим из callback data
        new_mode = callback.data.replace("toggle_pub_mode_", "")
        
        try:
            # Для режима расписания используем текущее расписание
            schedule = None
            if new_mode == 'schedule':
                schedule = [t.strftime("%H:%M") for t in self.controller.publication_schedule]
            
            # Для режима задержки используем текущую задержку
            delay = None
            if new_mode == 'delay':
                delay = self.controller.min_delay
            
            await self.controller.update_publication_settings(new_mode, schedule, delay)
            await callback.answer(f"✅ Режим изменен на {new_mode}")
            await self.show_publication_settings(callback)
        except Exception as e:
            logger.error(f"Ошибка изменения режима: {str(e)}")
            await callback.answer(f"❌ Ошибка: {str(e)}")
    
    async def handle_edit_schedule(self, callback: CallbackQuery) -> None:
        """Запрашивает ввод нового расписания"""
        user_id = callback.from_user.id
        self.pending_input[user_id] = {
            'param': 'publication_schedule',
            'type': 'publication',
            'chat_id': callback.message.chat.id,
        }
        self.pending_input_timeouts[user_id] = time.time() + 300
        
        current_schedule = ", ".join(
            [t.strftime("%H:%M") for t in self.controller.publication_schedule]
        ) if self.controller else ""
        
        await callback.message.answer(
            f"✏️ Введите новое расписание (формат: ЧЧ:ММ, ЧЧ:ММ, ...)\n"
            f"Текущее расписание: {current_schedule}\n"
            "Пример: 9:30, 12:00, 18:45",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit_publication")
            ]])
        )
        await callback.answer()

    async def handle_edit_delay(self, callback: CallbackQuery) -> None:
        """Запрашивает ввод новой задержки"""
        user_id = callback.from_user.id
        self.pending_input[user_id] = {
            'param': 'min_delay_between_posts',
            'type': 'publication',
            'chat_id': callback.message.chat.id,
        }
        self.pending_input_timeouts[user_id] = time.time() + 300
        
        current_delay = self.controller.min_delay if self.controller else ""
        
        await callback.message.answer(
            f"✏️ Введите минимальную задержку между постами (в секундах)\n"
            f"Текущая задержка: {current_delay} сек\n"
            "Пример: 300 (или 5m)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit_publication")
            ]])
        )
        await callback.answer()

    async def cancel_general_edit(self, callback: CallbackQuery) -> None:
        """Отменяет редактирование общих настроек"""
        try:
            user_id = callback.from_user.id
            
            # Сбрасываем состояние редактирования в UI
            if hasattr(self.ui, 'cancel_general_edit'):
                await self.ui.cancel_general_edit(user_id)
            
            # Очищаем состояние ожидания ввода
            if user_id in self.pending_input:
                del self.pending_input[user_id]
            if user_id in self.pending_input_timeouts:
                del self.pending_input_timeouts[user_id]
            if user_id in self.pending_input_retries:
                del self.pending_input_retries[user_id]
            
            # Возвращаемся в меню общих настроек
            await self.show_general_settings(callback)
            await callback.answer("❌ Редактирование отменено")
            
        except Exception as e:
            logger.error(f"Ошибка отмены редактирования: {str(e)}", exc_info=True)
            await callback.answer("⚠️ Ошибка отмены операции")

    async def handle_cancel_edit(self, callback: CallbackQuery) -> None:
        """Универсальная отмена редактирования для всех типов настроек"""
        try:
            user_id = callback.from_user.id
            
            # Удаляем состояние ожидания ввода
            if user_id in self.pending_input:
                input_data = self.pending_input[user_id]
                del self.pending_input[user_id]
                
                # Определяем, куда вернуть пользователя после отмены
                if input_data.get('type') == 'publication':
                    # Возвращаем в меню публикации
                    await self.show_publication_settings(callback)
                elif input_data.get('type') == 'ai':
                    # Возвращаем в настройки AI
                    await self.show_ai_settings(callback)
                elif input_data.get('type') == 'general':
                    # Возвращаем в общие настройки
                    await self.show_general_settings(callback)
                else:
                    # Возвращаем в главное меню
                    await self.send_main_menu(user_id, callback.message.chat.id)
            
            # Очищаем таймауты и счетчики попыток
            if user_id in self.pending_input_timeouts:
                del self.pending_input_timeouts[user_id]
            if user_id in self.pending_input_retries:
                del self.pending_input_retries[user_id]
            
            await callback.answer("❌ Редактирование отменено")
            
        except Exception as e:
            logger.error(f"Ошибка отмены редактирования: {str(e)}", exc_info=True)
            await callback.answer("⚠️ Ошибка отмены операции")

    async def edit_ai_settings(self, callback: CallbackQuery) -> None:
        """Переходит в режим редактирования настроек AI"""
        await self.ui.start_ai_edit(callback.from_user.id)
        await self.show_ai_settings(callback, edit_mode=True)
        await callback.answer()

    async def edit_ai_param(self, callback: CallbackQuery) -> None:
        """Обрабатывает выбор параметра для редактирования"""
        param_type = callback.data.replace("edit_ai_", "")
        user_id = callback.from_user.id
        
        if param_type == "model":
            keyboard = await self.ui.ai_model_selector(user_id)
            text = "Выберите модель:"
        elif param_type == "temp":
            keyboard = await self.ui.ai_temp_selector(user_id)
            text = "Выберите температуру (0.1-1.0):"
        elif param_type == "tokens":
            keyboard = await self.ui.ai_tokens_selector(user_id)
            text = "Выберите максимальное количество токенов:"
        else:
            await callback.answer("Неизвестный параметр")
            return
        
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        await callback.answer()

    async def set_ai_model(self, callback: CallbackQuery) -> None:
        """Устанавливает выбранную модель"""
        model = callback.data.split(":")[1]
        await self.ui.update_ai_setting(callback.from_user.id, "model", model)
        await self.show_ai_settings(callback, edit_mode=True)
        await callback.answer(f"Модель изменена на {model}")

    async def toggle_ai_enabled(self, callback: CallbackQuery) -> None:
        """Переключает состояние ИИ"""
        await self.ui.update_ai_setting(callback.from_user.id, "enabled", None)
        await self.show_ai_settings(callback, edit_mode=True)
        await callback.answer("Состояние ИИ изменено")

    async def set_ai_temp(self, callback: CallbackQuery) -> None:
        """Устанавливает температуру из предустановленных значений"""
        temp = float(callback.data.split(":")[1])
        await self.ui.update_ai_setting(callback.from_user.id, "temperature", temp)
        await self.show_ai_settings(callback, edit_mode=True)
        await callback.answer(f"Температура изменена на {temp}")

    async def set_ai_temp_custom(self, callback: CallbackQuery) -> None:
        """Запрашивает ручной ввод температуры"""
        user_id = callback.from_user.id
        
        self.pending_input[user_id] = {
            'param': 'temperature',
            'type': 'ai',
            'chat_id': callback.message.chat.id,
        }
        self.pending_input_timeouts[user_id] = time.time() + 300
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit_ai")]
            ]
        ])
        
        await callback.message.answer(
            "✏️ Введите значение температуры (0.1-1.0):\nПример: 0.7",
            reply_markup=keyboard
        )
        await callback.answer()

    async def set_ai_tokens(self, callback: CallbackQuery) -> None:
        """Устанавливает токены из предустановленных значений"""
        tokens = int(callback.data.split(":")[1])
        await self.ui.update_ai_setting(callback.from_user.id, "max_tokens", tokens)
        await self.show_ai_settings(callback, edit_mode=True)
        await callback.answer(f"Макс. токенов изменено на {tokens}")

    async def set_ai_tokens_custom(self, callback: CallbackQuery) -> None:
        """Запрашивает ручной ввод количества токенов"""
        user_id = callback.from_user.id
        
        self.pending_input[user_id] = {
            'param': 'max_tokens',
            'type': 'ai',
            'chat_id': callback.message.chat.id,
        }
        self.pending_input_timeouts[user_id] = time.time() + 300
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit_ai")]
            ]
        ])
        
        await callback.message.answer(
            "✏️ Введите максимальное количество токенов (500-10000):\nПример: 2500",
            reply_markup=keyboard
        )
        await callback.answer()

    async def save_ai_settings(self, callback: CallbackQuery) -> None:
        """Сохраняет изменения настроек AI"""
        try:
            changes = await self.ui.save_ai_settings(callback.from_user.id)
            
            if not changes:
                await callback.answer("Настройки не изменены")
                await self.show_ai_settings(callback)
                return
            
            # Применяем изменения в конфигурации
            for param, value in changes.items():
                self.config.update_param(param, value)
                logger.info(f"Параметр {param} изменен на {value}")
            
            # Формируем сообщение об изменениях
            changes_text = "\n".join([f"• {param}: {value}" for param, value in changes.items()])
            text = f"✅ Настройки успешно обновлены:\n\n{changes_text}"
            
            await callback.message.edit_text(
                text=text,
                parse_mode="HTML"
            )
            await asyncio.sleep(3)
            await self.show_ai_settings(callback)
            
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек AI: {str(e)}")
            await callback.answer("Ошибка сохранения настроек", show_alert=True)

    async def cancel_ai_edit(self, callback: CallbackQuery) -> None:
        """Отменяет редактирование настроек AI"""
        await self.ui.cancel_ai_edit(callback.from_user.id)
        await self.show_ai_settings(callback)
        await callback.answer("Редактирование отменено")

    # RSS настройки
    async def show_rss_settings(self, callback: CallbackQuery):
        """Показывает настройки RSS"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        feeds = self.controller.get_rss_status()
        text, keyboard = await self.ui.rss_settings_view(feeds)
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def start_rss_add(self, callback: CallbackQuery):
        """Начало добавления RSS"""
        keyboard = await self.ui.rss_add_dialog()
        await callback.message.edit_text(
            "Введите URL новой RSS-ленты:",
            reply_markup=keyboard
        )
        # Ожидание ввода реализуется в handle_message
    
    async def start_rss_remove(self, callback: CallbackQuery):
        """Начало удаления RSS"""
        feeds = self.controller.get_rss_status()
        keyboard = await self.ui.rss_remove_selector(feeds)
        await callback.message.edit_text(
            "Выберите ленту для удаления:",
            reply_markup=keyboard
        )
    
    async def confirm_rss_remove(self, callback: CallbackQuery):
        """Подтверждение удаления RSS"""
        try:
            index = int(callback.data.split("_")[-1])
            
            # Валидация индекса
            if index < 0 or index >= len(self.config.RSS_URLS):
                await callback.answer("❌ Неверный индекс ленты")
                return
                
            removed = self.config.RSS_URLS.pop(index)
            
            # Удаляем соответствующий статус активности
            if index < len(self.config.RSS_ACTIVE):
                self.config.RSS_ACTIVE.pop(index)
            
            # Сохраняем изменения в контроллере и .env
            if self.controller:
                self.controller.update_rss_state(
                    self.config.RSS_URLS,
                    self.config.RSS_ACTIVE
                )
            
            await callback.answer(f"✅ RSS удалена: {removed}")
            await self.show_rss_settings(callback)  # Обновляем интерфейс
        except (IndexError, ValueError) as e:
            logger.error(f"Ошибка удаления RSS: {str(e)}")
            await callback.answer("❌ Ошибка удаления ленты")
    
    async def toggle_rss_feed(self, callback: CallbackQuery):
        """Включение/выключение RSS-ленты"""
        try:
            # Извлечение индекса и действия
            parts = callback.data.split("_")
            index = int(parts[2])
            action = parts[3]
        except (IndexError, ValueError) as e:
            logger.error(f"Ошибка парсинга: {callback.data} - {str(e)}")
            await callback.answer("❌ Ошибка формата команды")
            return
        
        # Логика активации/деактивации
        success = await self.controller.toggle_rss_feed(index, action == "enable")
        
        if success:
            status = "активирована" if action == "enable" else "деактивирована"
            await callback.answer(f"✅ Лента {index+1} {status}")
        else:
            await callback.answer("❌ Ошибка изменения статуса")
        
        await self.show_rss_settings(callback)

    async def refresh_rss_status(self, callback: CallbackQuery):
        """Обновление статуса RSS"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
        
        changed = await self.controller.refresh_rss_status()
        if changed:
            await callback.answer("Статус RSS обновлен")
            await self.show_rss_settings(callback)
        else:
            await callback.answer("Данные не изменились")

    async def handle_retry_input(self, callback: CallbackQuery):
        """Повторный запрос ввода после ошибки"""
        user_id = callback.from_user.id
        param = callback.data.replace("retry_", "")
        
        if user_id not in self.pending_input:
            await callback.answer("❌ Сессия ввода утеряна")
            return
            
        input_data = self.pending_input[user_id]
        
        await callback.message.answer(
            f"✏️ Введите новое значение для '{param}':\n(Ошибка: {input_data.get('last_error', '')})",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_edit_{input_data['type']}")]
            ])
        )
        await callback.answer()

    async def handle_message(self, message: Message) -> None:
        """Обработчик текстовых сообщений с подтверждением изменений"""
        if not await self.enforce_owner_access(message):
            return
            
        user_id = message.from_user.id
        text = message.text.strip()
        
        # Обработка ожидаемых вводов параметров
        if user_id in self.pending_input:
            input_data = self.pending_input[user_id]
            param = input_data['param']
            param_type = input_data.get('type', 'general')
            
            try:
                    # Удаляем ожидание ввода сразу (чтобы избежать рекурсии)
                    del self.pending_input[user_id]
                    
                    # Обработка параметров публикации
                    if param_type == 'publication':
                        if param == 'publication_schedule':
                            # Валидация расписания
                            times = self.validator.validate_schedule(text)
                            
                            # Конвертация в объекты времени
                            await self.controller.update_publication_settings(mode='schedule', schedule=times)
                            
                            await message.answer(f"✅ Расписание обновлено: {', '.join(times)}")
                            await self.show_publication_settings(message)
                            
                        elif param == 'min_delay_between_posts':
                            # Валидация задержки
                            value = self.validator.validate_interval(text)
                            
                            # Обновление настроек
                            await self.controller.update_publication_settings(
                                mode='delay',
                                delay=value
                            )
                            
                            await message.answer(f"✅ Задержка обновлена: {value} сек")
                            await self.show_publication_settings(message)
                            
                        # Сброс счетчика попыток
                        if user_id in self.pending_input_retries:
                            del self.pending_input_retries[user_id]
                        return
                    
                    # Обработка параметров AI
                    elif param_type == 'ai':
                        if param == 'temperature':
                            value = self.validator.validate_temperature(text)
                            await self.ui.update_ai_setting(user_id, "temperature", value)
                            await message.answer(f"✅ Установлено: {param} = {value}")
                            await self.show_ai_settings(message, edit_mode=True)
                            
                        elif param == 'max_tokens':
                            value = self.validator.validate_tokens(text)
                            await self.ui.update_ai_setting(user_id, "max_tokens", value)
                            await message.answer(f"✅ Установлено: {param} = {value}")
                            await self.show_ai_settings(message, edit_mode=True)
                            
                        # Сброс счетчика попыток
                        if user_id in self.pending_input_retries:
                            del self.pending_input_retries[user_id]
                        return
                    
                    # Обработка общих параметров
                    elif param_type == 'general':
                        if param in ['temperature', 'yagpt_temperature']:
                            value = self.validator.validate_temperature(text)
                        elif param in ['max_tokens', 'yagpt_max_tokens']:
                            value = self.validator.validate_tokens(text)
                        elif param in ['check_interval', 'min_delay_between_posts']:
                            value = self.validator.validate_interval(text)
                        elif param in ['enable_yagpt', 'image_fallback']:
                            value = self.validator.validate_boolean(text)
                        else:
                            # Общая валидация для числовых параметров
                            min_val, max_val = 1, 10000
                            value = self.validator.validate_integer(text, min_val, max_val)
                        
                        # Обновление параметра
                        if param_type == 'ai':
                            await self.ui.update_ai_setting(user_id, param, value)
                            await self.show_ai_settings(message, edit_mode=True)
                        elif param_type == 'general':
                            await self.ui.update_general_setting(user_id, param, value)
                            await self.show_general_settings(message, edit_mode=True)
                            
                        await message.answer(f"✅ Установлено: {param} = {value}")
                        
                        # Сброс счетчика попыток
                        if user_id in self.pending_input_retries:
                            del self.pending_input_retries[user_id]
                        
            except ValueError as e:
                    # Сохраняем контекст для повторной попытки
                    input_data['last_error'] = str(e)
                    self.pending_input[user_id] = input_data
                    
                    # Счетчик попыток
                    retries = self.pending_input_retries.get(user_id, 0) + 1
                    self.pending_input_retries[user_id] = retries
                    
                    if retries >= 3:
                        await message.answer(f"❌ Слишком много ошибок. Операция отменена.\nОшибка: {str(e)}")
                        del self.pending_input[user_id]
                        del self.pending_input_retries[user_id]
                        return
                        
                    # Клавиатура с кнопкой отмены
                    cancel_data = f"cancel_edit_{param_type}"
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(text="↩️ Повторить ввод", callback_data=f"retry_{param}"),
                            InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_data)
                        ]
                    ])
                    
                    await message.answer(
                        f"❌ Ошибка: {str(e)}\n\nПопробуйте еще раз:",
                        reply_markup=keyboard
                    )
                    return
                    
            except Exception as e:
                    logger.error(f"Ошибка обработки ввода: {str(e)}")
                    await message.answer("❌ Произошла ошибка при обработке значения")
                    return
            
            # Обработка добавления RSS
            if message.reply_to_message and ("rss" in message.reply_to_message.text.lower() or "лент" in message.reply_to_message.text.lower()):
                url = text
                if not url.startswith(('http://', 'https://')):
                    await message.answer("❌ Некорректный URL. Должен начинаться с http:// или https://")
                    return
                    
                if url in self.config.RSS_URLS:
                    await message.answer("⚠️ Эта RSS-лента уже есть в списке")
                    return
                    
                try:
                    self.config.RSS_URLS.append(url)
                    self.config.RSS_ACTIVE.append(True)
                    self.config.save_to_env_file("RSS_URLS", json.dumps(self.config.RSS_URLS))
                    self.config.save_to_env_file("RSS_ACTIVE", json.dumps(self.config.RSS_ACTIVE))
                    
                    if self.controller:
                        self.controller.update_rss_state(self.config.RSS_URLS, self.config.RSS_ACTIVE)
                    
                    await message.answer(f"✅ RSS-лента успешно добавлена:\n{url}")
                    
                    # Показываем обновленный список
                    if self.controller:
                        feeds = self.controller.get_rss_status()
                        text, keyboard = await self.ui.rss_settings_view(feeds)
                        await message.answer("📋 Обновленный список RSS-лент:", reply_markup=keyboard)
                except Exception as e:
                    logger.error(f"Ошибка добавления RSS: {str(e)}")
                    await message.answer(f"❌ Ошибка при добавлении RSS-ленты:\n{str(e)}")
                return
            
            # Если сообщение не распознано как ввод параметра
            await self.send_main_menu(user_id, message.chat.id)
        
    async def show_rss_settings(self, callback: CallbackQuery, edit_mode: bool = False):
        """Показывает настройки RSS с возможностью редактирования"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        feeds = self.controller.get_rss_status()
        text, keyboard = await self.ui.rss_settings_view(feeds, edit_mode)
        
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=keyboard
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                logger.debug("Skipping unchanged RSS status")
            else:
                raise
        except Exception:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                reply_markup=keyboard
            )

    async def show_notify_settings(self, callback: CallbackQuery) -> None:
        """Показывает настройки уведомлений"""
        text = (
            "🔔 <b>Настройки уведомлений</b>\n\n"
            "Здесь будут настройки уведомлений\n"
            "Функция в разработке"
        )
        
        keyboard = await self.ui.back_to_settings()
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    async def handle_start(self, message: Message) -> None:
        if not await self.enforce_owner_access(message):
            return
        
        # Создаем фейковый callback для использования show_main_menu
        class FakeCallback:
            def __init__(self, message):
                self.from_user = message.from_user
                self.message = message
        
        await self.show_main_menu(FakeCallback(message))
    
    async def send_main_menu(self, user_id: int, chat_id: int) -> None:
        """Отправляет главное меню"""
        keyboard = await self.ui.main_menu(user_id)
        if not keyboard:
            return  # Уже обработано в ui
        
        await self.bot.send_message(
            chat_id=chat_id,
            text="🤖 <b>Управление RSS Ботом</b>\n\nВыберите действие:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    async def show_main_menu(self, callback: CallbackQuery) -> None:
        """Показывает главное меню, редактируя текущее сообщение"""
        user_id = callback.from_user.id
        keyboard = await self.ui.main_menu(user_id)
        if not keyboard:
            return
        
        try:
            # Пытаемся отредактировать текущее сообщение
            await callback.message.edit_text(
                text="🤖 <b>Управление RSS Ботом</b>\n\nВыберите действие:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            # Если редактирование невозможно, удаляем старое сообщение и отправляем новое
            try:
                await callback.message.delete()
            except Exception as e:
                logger.error(f"Ошибка удаления сообщения: {str(e)}")
            
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text="🤖 <b>Управление RSS Ботом</b>\n\nВыберите действие:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    
    async def show_statistics(self, callback: CallbackQuery) -> None:
        """Отображает статистику"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        stats = self.controller.stats
        text, media = await self.ui.stats_visualization(stats)
        
        if media:
            await self.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=media.media,
                caption=text,
                parse_mode="HTML"
            )
        else:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                parse_mode="HTML"
            )
    
    async def show_settings_menu(self, callback: CallbackQuery) -> None:
        """Показывает меню настроек"""
        keyboard = await self.ui.settings_menu(callback.from_user.id)
        
        try:
            await callback.message.edit_text(
                "⚙️ <b>Настройки бота</b>\n\nВыберите категорию:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text="⚙️ <b>Настройки бота</b>\n\nВыберите категорию:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    
    async def show_image_settings(self, callback: CallbackQuery) -> None:
        """Показывает настройки изображений"""
        text, media = await self.ui.image_settings_view(callback.from_user.id)
        
        if media:
            await self.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=media.media,
                caption=text,
                parse_mode="HTML"
            )
        else:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                parse_mode="HTML"
            )
    
    async def show_theme_selector(self, callback: CallbackQuery) -> None:
        """Показывает выбор тем оформления"""
        keyboard = await self.ui.theme_selector(callback.from_user.id)
        
        try:
            await callback.message.edit_text(
                "🎨 <b>Выбор темы оформления</b>\n\nВыберите стиль интерфейса:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            await self.bot.send_message(
                chat_id=callback.message.chat.id,
                text="🎨 <b>Выбор темы оформления</b>\n\nВыберите стиль интерфейса:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    
    async def handle_start_bot(self, callback: CallbackQuery) -> None:
        """Обработка запуска бота"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        # Проверка состояния
        if self.controller.is_running:
            await callback.answer("✅ Бот уже запущен", show_alert=True)
            return
        
        await self.ui.animated_processing(callback.message, "Запуск бота")
        await self.controller.start()
        await callback.answer("✅ Бот успешно запущен", show_alert=True)

    async def handle_stop_bot(self, callback: CallbackQuery) -> None:
        """Обработка остановки бота"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
            
        # Проверка состояния перед остановкой
        if not self.controller.is_running:
            await callback.answer("⏸ Бот уже остановлен", show_alert=True)
            return
        
        await self.ui.animated_processing(callback.message, "Остановка бота")
        await self.controller.stop()
        await callback.answer("⏸ Бот остановлен", show_alert=True)

    async def handle_status(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if not self.controller:
            await message.answer("⚠️ Контроллер не подключен")
            return
            
        status = self.controller.get_status_text()
        await message.answer(status, parse_mode="HTML")

    async def handle_stats(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if not self.controller or not hasattr(self.controller, 'stats'):
            await message.answer("⚠️ Статистика недоступна")
            return
            
        stats = (
            "📊 <b>Статистика:</b>\n"
            f"Постов: {self.controller.stats.get('posts_sent', 0)}\n"
            f"Ошибок: {self.controller.stats.get('errors', 0)}\n"
            f"Изображений: {self.controller.stats.get('images_generated', 0)}\n"
            f"Дубликатов отклонено: {self.controller.stats.get('duplicates_rejected', 0)}\n"
            f"Использований YandexGPT: {self.controller.stats.get('yagpt_used', 0)}\n"
            f"Ошибок YandexGPT: {self.controller.stats.get('yagpt_errors', 0)}"
        )
        await message.answer(stats, parse_mode="HTML")

    async def handle_rss_list(self, message: Message) -> None:
        """Отправляет список RSS-лент"""
        if not await self.enforce_owner_access(message):
            return
            
        try:
            if not self.controller:
                await message.answer("⚠️ Контроллер не подключен")
                return
                
            feeds = self.controller.get_rss_status()
            lines = ["📡 <b>Статус RSS-лент</b>\n"]
            
            for i, feed in enumerate(feeds, 1):
                status_icon = '🟢' if feed.get('active', True) else '🔴'
                error_icon = f" | ❗️ {feed.get('error_count', 0)}" if feed.get('error_count', 0) > 0 else ""
                last_check = f" | 📅 {feed.get('last_check', 'никогда')}" if feed.get('last_check') else ""
                lines.append(f"{i}. {status_icon} {feed['url'][:50]}...{error_icon}{last_check}")
            
            # Исправлено создание клавиатуры
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="◀️ Назад в меню", callback_data="main_menu")
                ]
            ])
            
            await message.answer(
                text="\n".join(lines),
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error showing RSS list: {str(e)}")
            await message.answer("Ошибка получения списка лент")
            
    async def enforce_owner_access(self, message_or_callback: Union[Message, CallbackQuery]) -> bool:
        """Проверяет доступ и уведомляет о попытках несанкционированного доступа"""
        user_id = message_or_callback.from_user.id
        if user_id == self.config.OWNER_ID:
            return True
            
        # Логирование и уведомление
        username = f"@{message_or_callback.from_user.username}" if message_or_callback.from_user.username else "без username"
        logger.warning(f"Unauthorized access attempt: UserID={user_id} {username}")
        
        # Отправка предупреждения владельцу
        try:
            await self.bot.send_message(
                chat_id=self.config.OWNER_ID,
                text=f"⚠️ *Попытка доступа!*\n"
                    f"• Пользователь: {username}\n"
                    f"• ID: `{user_id}`\n"
                    f"• Команда: `{getattr(message_or_callback, 'text', message_or_callback.data)}`",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send owner alert: {e}")
        
        # Ответ нарушителю
        try:
            if isinstance(message_or_callback, Message):
                await message_or_callback.answer("🚫 Доступ запрещен!")
            else:
                await message_or_callback.answer("🚫 Доступ запрещен!", show_alert=True)
        except:
            pass
        
        return False
    
    async def is_owner(self, message: Message) -> bool:
        return message.from_user.id == self.config.OWNER_ID

    async def handle_rss_add(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        args = message.text.split()
        if len(args) < 2:
            await message.answer("❌ Укажите URL RSS-ленты")
            return
        
        new_url = args[1]
        if new_url in self.config.RSS_URLS:
            await message.answer("⚠️ Эта RSS-лента уже есть в списке")
            return
    
        self.config.RSS_URLS.append(new_url)
        self.config.RSS_ACTIVE.append(True)  # Добавляем как активную
        await message.answer(f"✅ RSS-лента добавлена: {new_url}")

    async def handle_rss_remove(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        args = message.text.split()
        if len(args) < 2:
            await message.answer("❌ Укажите номер RSS-ленты для удаления")
            return
        
        try:
            index = int(args[1]) - 1
            if 0 <= index < len(self.config.RSS_URLS):
                removed = self.config.RSS_URLS.pop(index)
                
                if index < len(self.config.RSS_ACTIVE):
                    self.config.RSS_ACTIVE.pop(index)
                
                await message.answer(f"✅ RSS-лента удалена: {removed}")
            else:
                await message.answer("❌ Неверный номер RSS-ленты")
        except ValueError:
            await message.answer("❌ Укажите корректный номер")

    async def handle_pause(self, message: Message) -> None:
        """Обработчик команды остановки бота"""
        if not await self.is_owner(message):
            return
            
        if not self.controller:
            await message.answer("⚠️ Контроллер не подключен")
            return
            
        # Проверка состояния перед остановкой
        if self.controller.is_running:
            await self.controller.stop()
            await message.answer("⏸️ Публикации остановлены")
            
            # Обновляем статус в БД
            await self.db.update_bot_status(False)
        else:
            await message.answer("ℹ️ Бот уже остановлен")

    async def handle_resume(self, message: Message) -> None:
        """Обработчик команды запуска бота"""
        if not await self.is_owner(message):
            return
            
        if not self.controller:
            await message.answer("⚠️ Контроллер не подключен")
            return
            
        # Проверка состояния перед запуском
        if not self.controller.is_running:
            await self.controller.start()
            await message.answer("▶️ Публикации возобновлены")
            
            # Обновляем статус в БД
            await self.db.update_bot_status(True)
        else:
            await message.answer("ℹ️ Бот уже работает")

    async def handle_resume_cmd(self, callback: CallbackQuery) -> None:
        """Обработка кнопки /resume"""
        # Создаем фейковое сообщение для обработчика
        class FakeMessage:
            def __init__(self, callback):
                self.from_user = callback.from_user
                self.chat = callback.message.chat
                self.text = "/resume"
                
        await self.handle_resume(FakeMessage(callback))

    async def handle_pause_cmd(self, callback: CallbackQuery) -> None:
        """Обработка кнопки /pause"""
        # Создаем фейковое сообщение для обработчика
        class FakeMessage:
            def __init__(self, callback):
                self.from_user = callback.from_user
                self.chat = callback.message.chat
                self.text = "/pause"
                
        await self.handle_pause(FakeMessage(callback))

    async def handle_settings(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        source_mapping = {
            'template': 'Шаблоны',
            'original': 'Оригиналы',
            'none': 'Нет'
        }
        
        settings = (
            "⚙️ <b>Текущие настройки:</b>\n"
            f"YandexGPT: {'🟢 Вкл' if self.config.ENABLE_YAGPT else '🔴 Выкл'}\n"
            f"Изображения: {'🟢 Вкл' if self.config.ENABLE_IMAGE_GENERATION else '🔴 Выкл'}\n"
            f"Источник изображений: {source_mapping.get(self.config.IMAGE_SOURCE, 'Неизвестно')}\n"
            f"Резервная генерация: {'🟢 Вкл' if self.config.IMAGE_FALLBACK else '🔴 Выкл'}\n"
            f"Постов/час: {self.config.POSTS_PER_HOUR}\n"
            f"Модель YandexGPT: {self.config.YAGPT_MODEL}"
        )
        await message.answer(settings, parse_mode="HTML")

    async def handle_set(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Используйте: /set [параметр] [значение]")
            return
        
        param = args[1].upper()
        value = " ".join(args[2:])
        
        ALLOWED_PARAMS = {
            'POSTS_PER_HOUR': {'type': int, 'validator': lambda x: 1 <= x <= 60, 'error_msg': 'Должно быть целое число от 1 до 60'},
            'MIN_DELAY_BETWEEN_POSTS': {'type': int, 'validator': lambda x: x >= 10, 'error_msg': 'Минимальная задержка 10 секунд'},
            'CHECK_INTERVAL': {'type': int, 'validator': lambda x: x >= 60, 'error_msg': 'Интервал проверки не менее 60 секунд'},
            'ENABLE_IMAGE_GENERATION': {'type': bool, 'validator': None},
            'ENABLE_YAGPT': {'type': bool, 'validator': None},
            'YAGPT_MODEL': {'type': str, 'validator': lambda x: x in ['yandexgpt-lite', 'yandexgpt-pro'], 'error_msg': 'Допустимые модели: yandexgpt-lite, yandexgpt-pro'},
            'YAGPT_TEMPERATURE': {'type': float, 'validator': lambda x: 0.1 <= x <= 1.0, 'error_msg': 'Температура должна быть от 0.1 до 1.0'}
        }
        
        if param not in ALLOWED_PARAMS:
            await message.answer(f"❌ Параметр {param} недоступен для изменения")
            return
        
        param_info = ALLOWED_PARAMS[param]
        param_type = param_info['type']
        
        try:
            if param_type is bool:
                converted_value = value.lower() in ['true', '1', 'yes', 'on']
            else:
                converted_value = param_type(value)
            
            if param_info['validator'] and not param_info['validator'](converted_value):
                raise ValueError(param_info['error_msg'])
            
            setattr(self.config, param, converted_value)
            await message.answer(f"✅ Параметр {param} обновлен на {value}")
            self.config.save_to_env_file(param, str(converted_value))
        except (TypeError, ValueError) as e:
            await message.answer(f"❌ Ошибка: {str(e)}")

    async def handle_set_schedule(self, message: Message) -> None:
        """Обработчик команды /set_schedule"""
        # Проверка прав доступа
        if not await self.enforce_owner_access(message):
            return
            
        # Проверка наличия контроллера
        if not self.controller:
            await message.answer("❌ Контроллер не инициализирован")
            return
            
        args = message.text.split(maxsplit=1)
        schedule_str = args[1].strip() if len(args) > 1 else None

        try:
            # Если аргументы не предоставлены, показываем текущие настройки
            if not schedule_str:
                current_settings = self.controller.get_publication_settings()
                schedule_times = current_settings['schedule']
                schedule_text = ', '.join(schedule_times)
                next_time = self.controller.next_scheduled_time.strftime('%H:%M') if self.controller.next_scheduled_time else "не рассчитано"
                
                response = (
                    "📅 Текущее расписание публикаций:\n"
                    f"Режим: {current_settings['mode']}\n"
                    f"Задержка: {current_settings['delay']} сек\n"
                    f"Времена: {schedule_text}\n"
                    f"Следующая публикация: {next_time}\n\n"
                    "Чтобы изменить расписание, используйте:\n"
                    "/set_schedule 9:30,12:00,18:45"
                )
                await message.answer(response)
                return

            # Проверяем и нормализуем формат времени
            schedule_list = [t.strip() for t in schedule_str.split(',')]
            validated_times = []
            
            for t in schedule_list:
                # Проверка формата времени
                if not re.match(r"^\d{1,2}:\d{2}$", t):
                    raise ValueError(f"❌ Неверный формат времени: '{t}'. Используйте ЧЧ:ММ")
                
                # Нормализация формата (добавляем ведущий ноль при необходимости)
                if re.match(r"^\d{1}:\d{2}$", t):
                    t = f"0{t}"  # "9:30" -> "09:30"
                    
                # Проверка валидности времени
                hour, minute = map(int, t.split(':'))
                if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                    raise ValueError(f"❌ Недопустимое время: {t}")
                    
                validated_times.append(t)
            
            # Обновление настроек в контроллере
            await self.controller.update_publication_settings(
                mode='schedule',
                schedule=validated_times
            )
            
            # Формируем список времен для ответа
            schedule_text = ', '.join(validated_times)
            next_time = self.controller.next_scheduled_time.strftime('%H:%M')
            await message.answer(f"✅ Расписание успешно обновлено: {schedule_text}")
            await message.answer(f"⏱ Следующая публикация в: {next_time}")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Ошибка установки расписания: {error_msg}")
            await message.answer(error_msg)

    async def show_publication_settings_menu(self, callback: CallbackQuery) -> None:
        """Показывает меню настроек публикации с кнопкой для расписания"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
        
        pub_mode = self.controller.publication_mode
        pub_mode_text = "Расписание" if pub_mode == 'schedule' else "Задержка"
        
        text = (
            "⚙️ <b>Настройки публикации</b>\n\n"
            f"• <b>Режим:</b> {pub_mode_text}\n"
        )
        
        if pub_mode == 'schedule':
            schedule = ", ".join([t.strftime("%H:%M") for t in self.controller.publication_schedule])
            text += f"• <b>Расписание:</b> {schedule}\n"
        else:
            text += f"• <b>Задержка:</b> {self.controller.min_delay} сек\n"
        
        # Создаем клавиатуру
        builder = InlineKeyboardBuilder()
        
        # Основные кнопки
        builder.button(text="📅 Управление расписанием", callback_data="manage_schedule")
        builder.button(text="🔄 Сменить режим публикации", callback_data="switch_publication_mode")
        builder.button(text="◀️ Назад в настройки", callback_data="settings")
        
        # Распределение кнопок по строкам
        builder.adjust(1)
        
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка показа меню публикации: {str(e)}")
            await callback.answer("Ошибка обновления меню")
        
    async def handle_show_schedule(self, callback: CallbackQuery) -> None:
        """Показывает текущее расписание"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
        
        schedule = self.controller.publication_schedule
        schedule_str = ", ".join([t.strftime("%H:%M") for t in schedule])
        
        text = (
            "⏰ <b>Текущее расписание публикаций:</b>\n"
            f"<code>{schedule_str}</code>\n\n"
            "Для изменения используйте кнопку ниже или команду:\n"
            "<code>/set_schedule 9:30,12:00,18:45</code>"
        )
        
        # Клавиатура с кнопкой изменения
        builder = InlineKeyboardBuilder()
        builder.button(text="✏️ Изменить расписание", callback_data="edit_schedule")
        builder.button(text="◀️ Назад", callback_data="manage_schedule")
        builder.adjust(1)
        
        await callback.message.edit_text(
            text=text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

    async def handle_edit_schedule(self, callback: CallbackQuery) -> None:
        """Запрашивает ввод нового расписания через UI"""
        user_id = callback.from_user.id
        self.pending_input[user_id] = {
            'param': 'publication_schedule',
            'type': 'publication',
            'chat_id': callback.message.chat.id,
        }
        self.pending_input_timeouts[user_id] = time.time() + 300
        
        current_schedule = ", ".join(
            [t.strftime("%H:%M") for t in self.controller.publication_schedule]
        ) if self.controller else ""
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit_publication")
        ]])
        
        text = (
            "✏️ <b>Введите новое расписание публикаций</b>\n\n"
            f"Текущее расписание: <code>{current_schedule}</code>\n\n"
            "• Формат: <b>ЧЧ:ММ,ЧЧ:ММ,...</b>\n"
            "• Пример: <code>9:30,12:00,18:45</code>\n"
            "• Минимум 1 время, максимум 24"
        )
        
        await callback.message.answer(
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await callback.answer()

    async def handle_switch_publication_mode(self, callback: CallbackQuery) -> None:
        """Предлагает выбор режима публикации"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
        
        # Создаем клавиатуру
        builder = InlineKeyboardBuilder()
        
        builder.button(text="⏱ Режим задержки", callback_data="set_mode_delay")
        builder.button(text="⏰ Режим расписания", callback_data="set_mode_schedule")
        builder.button(text="◀️ Назад", callback_data="publication_settings")
        
        builder.adjust(1)
        
        text = (
            "🔄 <b>Смена режима публикации</b>\n\n"
            f"Текущий режим: <b>{'Расписание' if self.controller.publication_mode == 'schedule' else 'Задержка'}</b>\n\n"
            "Выберите новый режим:"
        )
        
        await callback.message.edit_text(
            text=text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

    async def handle_set_publication_mode(self, callback: CallbackQuery) -> None:
        """Устанавливает новый режим публикации"""
        mode = callback.data.replace("set_mode_", "")
        
        try:
            if mode == "schedule":
                # При переходе в режим расписания используем текущее расписание
                schedule = [t.strftime("%H:%M") for t in self.controller.publication_schedule]
                await self.controller.update_publication_settings(mode, schedule=schedule)
            else:
                # При переходе в режим задержки используем текущую задержку
                delay = self.controller.min_delay
                await self.controller.update_publication_settings(mode, delay=delay)
            
            await callback.answer(f"✅ Режим изменен на {mode}")
            await self.show_publication_settings_menu(callback)
        except Exception as e:
            logger.error(f"Ошибка смены режима: {str(e)}")
            await callback.answer(f"❌ Ошибка: {str(e)}")

    async def handle_manage_schedule(self, callback: CallbackQuery) -> None:
        """Обработчик кнопки управления расписанием"""
        if not self.controller:
            await callback.answer("Контроллер не подключен")
            return
        
        # Создаем клавиатуру
        builder = InlineKeyboardBuilder()
        
        # Кнопки действий
        builder.button(text="✏️ Изменить расписание", callback_data="edit_schedule")
        builder.button(text="👁 Показать текущее расписание", callback_data="show_schedule")
        builder.button(text="◀️ Назад", callback_data="publication_settings")
        
        # Распределение кнопок
        builder.adjust(1)
        
        try:
            await callback.message.edit_text(
                "📅 <b>Управление расписанием публикаций</b>\n\nВыберите действие:",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка показа меню расписания: {str(e)}")
            await callback.answer("Ошибка обновления меню")

    async def show_help_menu(self, message: Message):
        """Показывает справку по формату команды"""
        help_text = (
            "❌ Неверный формат команды\n\n"
            "📝 Используйте:\n"
            "• Для установки расписания: `/set_schedule 9:30 12:00 18:45`\n"
            "• Для показа меню настроек: просто `/set_schedule`"
        )
        await message.reply(help_text)

    async def handle_set_mode(self, message: Message):
        """Обработчик команды /set_mode"""
        if not self.controller:
            await message.reply("❌ Контроллер не инициализирован")
            return
            
        try:
            mode = message.text.split()[1].lower()
            if mode not in ['schedule', 'delay']:
                raise ValueError("Недопустимый режим")
                
            self.controller.set_publication_mode(mode)
            await message.reply(f"✅ Режим изменен на '{mode}'")
        except Exception as e:
            logger.error(f"Ошибка смены режима: {str(e)}")
            await message.reply("❌ Используйте: /set_mode schedule или /set_mode delay")
    
    def set_controller(self, controller):
        """Устанавливает контроллер для обработки команд"""
        self.controller = controller
        logger.info("Контроллер установлен для Telegram бота")

    async def handle_clear_history(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        if not self.controller:
            await message.answer("⚠️ Контроллер не подключен")
            return
            
        try:
            self.controller.state.state['sent_entries'] = {}
            await message.answer("✅ История отправленных постов очищена! Бот будет повторно отправлять новости.")
        except Exception as e:
            logger.error(f"Error clearing history: {str(e)}")
            await message.answer(f"❌ Ошибка при очистке истории: {str(e)}")

    async def handle_params_list(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        params = []
        for name in dir(self.config):
            if name.isupper() and not name.startswith('_') and not callable(getattr(self.config, name)):
                value = getattr(self.config, name)
                value_type = type(value).__name__
                
                if isinstance(value, (list, tuple)) and len(value) > 3:
                    display_value = f"{value[:3]}... ({len(value)} items)"
                elif isinstance(value, str) and len(value) > 50:
                    display_value = value[:50] + "..."
                else:
                    display_value = str(value)
                    
                params.append(f"• <b>{name}</b>: {display_value}")
        
        chunk_size = 15
        for i in range(0, len(params), chunk_size):
            chunk = params[i:i + chunk_size]
            response = "⚙️ <b>Доступные параметры:</b>\n\n" + "\n".join(chunk)
            if i + chunk_size < len(params):
                response += "\n\n<i>Продолжение следует...</i>"
            await message.answer(response, parse_mode="HTML")

    async def handle_param_info(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        args = message.text.split()
        if len(args) < 2:
            await message.answer("❌ Укажите имя параметра")
            return
            
        param_name = args[1].upper()
        
        if not hasattr(self.config, param_name):
            await message.answer(f"❌ Параметр {param_name} не существует")
            return
            
        value = getattr(self.config, param_name)
        value_type = type(value).__name__
        
        type_description = {
            'int': 'целое число',
            'float': 'число с плавающей точкой',
            'bool': 'логическое значение (true/false)',
            'str': 'строка',
            'list': 'список значений (через запятую)',
            'tuple': 'кортеж чисел (через запятую)'
        }.get(value_type, value_type)
        
        examples = {
            int: "42",
            float: "3.14",
            bool: "true или false",
            str: "любая строка",
            list: "item1, item2, item3",
            tuple: "255, 255, 255"
        }.get(type(value), str(value))
        
        response = (
            f"ℹ️ <b>Информация о параметре:</b>\n\n"
            f"<b>Имя:</b> {param_name}\n"
            f"<b>Тип:</b> {value_type} ({type_description})\n"
            f"<b>Текущее значение:</b> {value}\n\n"
            f"<b>Примеры значений:</b>\n"
            f"{examples}\n\n"
            f"<b>Изменить командой:</b>\n"
            f"<code>/set_all {param_name} [новое_значение]</code>"
        )
        
        await message.answer(response, parse_mode="HTML")

    async def handle_set_all(self, message: Message) -> None:
        if not await self.is_owner(message):
            return
            
        args = message.text.split()
        if len(args) < 3:
            await message.answer("❌ Используйте: /set_all [параметр] [значение]")
            return
            
        param_name = args[1].upper()
        new_value_str = " ".join(args[2:])
        
        if not hasattr(self.config, param_name):
            await message.answer(f"❌ Параметр {param_name} не существует")
            return
            
        current_value = getattr(self.config, param_name)
        value_type = type(current_value)
        
        try:
            if value_type is bool:
                converted_value = new_value_str.lower() in ['true', '1', 'yes', 'y', 't', 'on']
            elif value_type is int:
                converted_value = int(new_value_str)
            elif value_type is float:
                converted_value = float(new_value_str)
            elif value_type is list:
                converted_value = [item.strip() for item in new_value_str.split(',')]
            elif value_type is tuple:
                converted_value = tuple(map(int, new_value_str.split(',')))
            elif value_type is str:
                converted_value = new_value_str
            else:
                converted_value = value_type(new_value_str)
            
            setattr(self.config, param_name, converted_value)
            self.config.save_to_env_file(param_name, str(converted_value))
            
            response = (
                f"✅ <b>Параметр успешно обновлен!</b>\n\n"
                f"<b>Параметр:</b> {param_name}\n"
                f"<b>Старое значение:</b> {current_value}\n"
                f"<b>Новое значение:</b> {converted_value}\n\n"
            )
            
            critical_params = ['TOKEN', 'CHANNEL_ID', 'OWNER_ID', 'YANDEX_API_KEY']
            if param_name in critical_params:
                response += "⚠️ <i>Для применения изменений может потребоваться перезагрузка бота</i>"
            
            await message.answer(response, parse_mode="HTML")
        except (TypeError, ValueError) as e:
            await message.answer(
                f"❌ <b>Ошибка преобразования значения:</b>\n"
                f"Параметр: {param_name}\n"
                f"Требуемый тип: {value_type.__name__}\n"
                f"Ошибка: {str(e)}",
                parse_mode="HTML"
            )

    async def close(self) -> None:
        if hasattr(self, 'cleanup_task'):
            self.cleanup_task.cancel()
        await self.bot.session.close()