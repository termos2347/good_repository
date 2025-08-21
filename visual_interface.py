from typing import Any, Optional, Dict, List
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    InputMediaPhoto,
    CallbackQuery,
    FSInputFile
)
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import Config
import asyncio
import os
import logging
import matplotlib.pyplot as plt
import numpy as np
from io import BytesIO
import time

logger = logging.getLogger('VisualInterface')

class UIBuilder:
    THEMES = {
        'default': {
            'primary': '🔵',
            'success': '🟢',
            'warning': '🟡',
            'error': '🔴',
            'text': '⚪'
        },
        'dark': {
            'primary': '🌑',
            'success': '🌑',
            'warning': '🌕',
            'error': '🔥',
            'text': '⚪'
        },
        'colorful': {
            'primary': '🌈',
            'success': '✅',
            'warning': '⚠️',
            'error': '❌',
            'text': '📝'
        }
    }
    
    # Новое поле для хранения временных настроек
    user_editing_states: Dict[int, Dict[str, Any]] = {}  # Для AI настроек
    user_general_editing_states: Dict[int, Dict[str, Any]] = {}  # Для основных настроек

    def __init__(self, config: Config):
        self.config = config
        self.user_themes = {}
    
    def get_theme(self, user_id: int) -> dict:
        return self.user_themes.get(user_id, self.THEMES['default'])
    
    async def main_menu(self, user_id: int) -> Optional[InlineKeyboardMarkup]:
        """Показывает главное меню только владельцу"""
        # Проверка прав доступа
        if user_id != self.config.OWNER_ID:
            return None
        
        theme = self.get_theme(user_id)
        
        # Основные кнопки меню
        buttons = [
            [
                InlineKeyboardButton(
                    text=f"{theme['primary']} Главная",
                    callback_data="main"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{theme['text']} Мониторинг",
                    callback_data="monitoring"
                ),
                InlineKeyboardButton(
                    text=f"{theme['text']} Настройки",
                    callback_data="settings"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{theme['text']} Статистика",
                    callback_data="stats"
                ),
                InlineKeyboardButton(
                    text=f"{theme['text']} RSS Ленты",
                    callback_data="rss_list"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{theme['success']} Запустить",
                    callback_data="start_bot"
                ),
                InlineKeyboardButton(
                    text=f"{theme['warning']} Остановить",
                    callback_data="stop_bot"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎨 Сменить тему",
                    callback_data="change_theme"
                )
            ]
        ]
        
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    async def back_to_settings(self) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(
            text="◀️ Назад",
            callback_data="settings"
        )
        return builder.as_markup()
    
    async def back_button(self) -> InlineKeyboardMarkup:
        """Кнопка 'Назад' для меню настроек"""
        builder = InlineKeyboardBuilder()
        builder.button(
            text="◀️ Назад",
            callback_data="settings"
        )
        return builder.as_markup()

    async def stats_visualization(self, stats: dict) -> tuple:
        """Генерирует визуализацию статистики"""
        try:
            # График активности по часам
            plt.figure(figsize=(10, 6))
            
            # Данные для графика (пример)
            hours = list(range(24))
            posts = [stats.get(f'hour_{h}', 0) for h in hours]
            
            plt.bar(hours, posts, color='#4CAF50')
            plt.title('Активность по часам')
            plt.xlabel('Часы')
            plt.ylabel('Посты')
            plt.xticks(hours)
            plt.grid(axis='y', alpha=0.5)
            
            summary = (
                "📊 <b>Статистика производительности</b>\n\n"
                f"▸ Постов отправлено: <b>{stats.get('posts_sent', 0)}</b>\n"
                f"▸ Ошибок: <b>{stats.get('errors', 0)}</b>\n"
                f"▸ Использований AI: <b>{stats.get('yagpt_used', 0)}</b>\n"
                f"▸ Изображений сгенерировано: <b>{stats.get('images_generated', 0)}</b>\n"
                f"▸ Среднее время цикла: <b>{stats.get('avg_processing_time', 0):.2f} сек</b>\n"
                f"▸ Аптайм: <b>{stats.get('uptime', '0:00')}</b>"
            )

            # В методе stats_visualization
            buf = BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight')
            buf.seek(0)
            image_data = buf.getvalue()  # Получаем байты
            photo = BufferedInputFile(image_data, filename="stats.png")  # Создаем InputFile
            return summary, InputMediaPhoto(media=photo, caption=summary)
            
        except Exception as e:
            logger.error(f"Stats visualization error: {str(e)}")
            return "📊 Статистика недоступна", None

    async def settings_menu(self, user_id: int) -> InlineKeyboardMarkup:
        theme = self.get_theme(user_id)
        builder = InlineKeyboardBuilder()
        
        builder.button(
            text=f"{theme['text']} Основные", 
            callback_data="settings_general"
        )
        builder.button(
            text=f"{theme['text']} Изображения", 
            callback_data="settings_images"
        )
        builder.button(
            text=f"{theme['text']} AI", 
            callback_data="settings_ai"
        )
        builder.button(
            text=f"{theme['text']} RSS", 
            callback_data="settings_rss"
        )
        builder.button(
            text=f"{theme['text']} Оповещения", 
            callback_data="settings_notify"
        )
        builder.button(
            text=f"{theme['primary']} Назад", 
            callback_data="main_menu"
        )
        
        builder.adjust(2, 2, 2, 1)
        return builder.as_markup()

    async def image_settings_view(self, user_id: int) -> tuple:
        """Возвращает визуальное представление настроек изображений"""
        theme = self.get_theme(user_id)
        text = (
            "🖼 <b>Текущие настройки изображений</b>\n\n"
            f"▸ Источник: <b>{self.config.IMAGE_SOURCE.capitalize()}</b>\n"
            f"▸ Резервная генерация: {'Вкл' if self.config.IMAGE_FALLBACK else 'Выкл'}\n"
            f"▸ Цвет текста: <code>{self.config.TEXT_COLOR}</code>\n"
            f"▸ Цвет обводки: <code>{self.config.STROKE_COLOR}</code>\n"
            f"▸ Ширина обводки: <b>{self.config.STROKE_WIDTH}px</b>"
        )
        
        # Создаем пример изображения
        try:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.new('RGB', (400, 200), (40, 40, 60))
            draw = ImageDraw.Draw(img)
            
            # Загрузка шрифта
            font_path = os.path.join(self.config.FONTS_DIR, self.config.DEFAULT_FONT)
            font = ImageFont.truetype(font_path, 32) if os.path.exists(font_path) else ImageFont.load_default()
            
            # Текст с текущими настройками
            draw.text(
                (200, 100), 
                "Пример текста", 
                fill=tuple(self.config.TEXT_COLOR),
                stroke_fill=tuple(self.config.STROKE_COLOR),
                stroke_width=self.config.STROKE_WIDTH,
                font=font,
                anchor="mm"
            )
            
            # Сохраняем в буфер
            # В методе image_settings_view
            buf = BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            image_data = buf.getvalue()
            photo = BufferedInputFile(image_data, filename="preview.png")
            return text, InputMediaPhoto(media=photo, caption=text)
            
        except Exception as e:
            logger.error(f"Preview generation failed: {str(e)}")
            return text, None

    async def theme_selector(self, user_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        
        for theme_name in self.THEMES:
            builder.button(
                text=f"{self.THEMES[theme_name]['primary']} {theme_name.capitalize()}",
                callback_data=f"set_theme_{theme_name}"
            )
        
        builder.button(
            text="◀️ Назад",
            callback_data="settings"
        )
        
        builder.adjust(2, 1)
        return builder.as_markup()

    async def progress_bar(self, current: int, total: int) -> str:
        """Генерирует текстовый прогресс-бар"""
        bar_length = 10
        filled = int(bar_length * current / total)
        empty = bar_length - filled
        return f"[{'■' * filled}{'□' * empty}] {current}/{total}"

    async def animated_processing(self, message, process_name: str, duration: int = 5):
        """Отображает анимированный процесс"""
        status_msg = await message.answer(f"🔄 {process_name}...")
        
        for i in range(1, 11):
            await asyncio.sleep(duration / 10)
            bar = "⬛" * i + "⬜" * (10 - i)
            await status_msg.edit_text(f"⏳ {process_name}\n{bar} {i*10}%")
        
        await status_msg.edit_text(f"✅ {process_name} завершено!")

    async def rss_feed_status(self, feeds: list) -> str:
        """Визуализация статуса RSS-лент"""
        lines = ["📡 <b>Статус RSS-лент</b>\n"]
        
        for feed in feeds:
            status_icon = '🟢' if feed.get('active', True) else '🔴'
            error_icon = f"❗️ {feed.get('error_count', 0)}" if feed.get('error_count', 0) > 0 else ""
            lines.append(f"{status_icon} {feed['url']} {error_icon}")
        
        return "\n".join(lines)
    
    async def general_settings_view(self, user_id: int, edit_mode: bool = False) -> tuple:
        """Визуализация основных настроек с режимом редактирования"""
        # Получаем текущие/временные настройки
        settings = self.user_general_editing_states.get(user_id, {
            'check_interval': self.config.CHECK_INTERVAL,
            'max_posts': self.config.MAX_POSTS_PER_CYCLE,
            'posts_per_hour': self.config.POSTS_PER_HOUR,
            'min_delay': self.config.MIN_DELAY_BETWEEN_POSTS
        })
        
        text = (
            "⚙️ <b>Основные настройки</b>\n\n"
            f"• Интервал проверки: {settings['check_interval']} сек {'✏️' if edit_mode else ''}\n"
            f"• Макс. постов за цикл: {settings['max_posts']} {'✏️' if edit_mode else ''}\n"
            f"• Постов в час: {settings['posts_per_hour']} {'✏️' if edit_mode else ''}\n"
            f"• Мин. задержка между постами: {settings['min_delay']} сек {'✏️' if edit_mode else ''}"
        )
        
        builder = InlineKeyboardBuilder()
        if edit_mode:
            builder.button(text="✏️ Интервал", callback_data="edit_general_check_interval")
            builder.button(text="✏️ Макс. постов", callback_data="edit_general_max_posts")
            builder.button(text="✏️ Постов/час", callback_data="edit_general_posts_per_hour")
            builder.button(text="✏️ Задержка", callback_data="edit_general_min_delay")
            builder.button(text="💾 Сохранить", callback_data="save_general_settings")
            builder.button(text="❌ Отмена", callback_data="cancel_general_edit")
            builder.adjust(2, 2, 1)
        else:
            builder.button(text="✏️ Редактировать", callback_data="edit_general_settings")
            builder.button(text="◀️ Назад", callback_data="settings")
        
        return text, builder.as_markup()

    async def general_param_selector(self, user_id: int, param: str) -> InlineKeyboardMarkup:
        """Клавиатура выбора значений для основных параметров"""
        current_value = self.config.__dict__.get(param.upper())
        if user_id in self.user_general_editing_states:
            current_value = self.user_general_editing_states[user_id].get(param, current_value)
        
        presets = {
            'check_interval': [60, 300, 600, 1800],
            'max_posts': [1, 3, 5, 10],
            'posts_per_hour': [10, 20, 30, 50],
            'min_delay': [10, 30, 60, 120]
        }
        
        builder = InlineKeyboardBuilder()
        for value in presets.get(param, []):
            builder.button(
                text=f"{'✅ ' if value == current_value else ''}{value}",
                callback_data=f"set_general_{param}:{value}"
            )
        
        builder.button(text="🔢 Вручную", callback_data=f"set_general_{param}_custom")
        builder.button(text="◀️ Назад", callback_data="edit_general_settings")
        builder.adjust(2, 2, 1)
        return builder.as_markup()
    
    async def start_general_edit(self, user_id: int):
        """Начинает редактирование основных настроек"""
        self.user_general_editing_states[user_id] = {
            'check_interval': self.config.CHECK_INTERVAL,
            'max_posts': self.config.MAX_POSTS_PER_CYCLE,
            'posts_per_hour': self.config.POSTS_PER_HOUR,
            'min_delay': self.config.MIN_DELAY_BETWEEN_POSTS
        }
    
    async def update_general_setting(self, user_id: int, param: str, value: Any):
        """Обновляет временную настройку"""
        if user_id in self.user_general_editing_states:
            self.user_general_editing_states[user_id][param] = value
    
    async def save_general_settings(self, user_id: int) -> Dict[str, Any]:
        """Сохраняет настройки и возвращает изменения"""
        if user_id not in self.user_general_editing_states:
            return {}
        
        changes = {}
        settings = self.user_general_editing_states.pop(user_id)
        
        if settings['check_interval'] != self.config.CHECK_INTERVAL:
            changes['CHECK_INTERVAL'] = settings['check_interval']
        
        if settings['max_posts'] != self.config.MAX_POSTS_PER_CYCLE:
            changes['MAX_POSTS_PER_CYCLE'] = settings['max_posts']
        
        if settings['posts_per_hour'] != self.config.POSTS_PER_HOUR:
            changes['POSTS_PER_HOUR'] = settings['posts_per_hour']
        
        if settings['min_delay'] != self.config.MIN_DELAY_BETWEEN_POSTS:
            changes['MIN_DELAY_BETWEEN_POSTS'] = settings['min_delay']
        
        return changes
    
    async def cancel_general_edit(self, user_id: int) -> None:
        """Сбрасывает состояние редактирования общих настроек"""
        if user_id in self.user_general_editing_states:
            del self.user_general_editing_states[user_id]
        logger.debug(f"Сброшено состояние редактирования для {user_id}")
    
    async def cancel_ai_edit(self, user_id: int) -> None:
        """Сбрасывает состояние редактирования AI настроек"""
        if user_id in self.ai_edit_states:
            del self.ai_edit_states[user_id]
        logger.debug(f"Сброшено состояние AI редактирования для {user_id}")

    async def ai_settings_view(self, user_id: int, edit_mode: bool = False) -> tuple:
        """Возвращает текст и клавиатуру для настроек AI"""
        # Получаем текущие или временные настройки
        if edit_mode and user_id in self.user_editing_states:
            settings = self.user_editing_states[user_id]
        else:
            settings = {
                'enabled': self.config.ENABLE_YAGPT,
                'model': self.config.YAGPT_MODEL,
                'temperature': self.config.YAGPT_TEMPERATURE,
                'max_tokens': self.config.YAGPT_MAX_TOKENS
            }

        theme = self.get_theme(user_id)
        text = (
            "🧠 <b>Настройки YandexGPT</b>\n\n"
            f"• Состояние: {'🟢 Включен' if settings['enabled'] else '🔴 Выключен'}\n"
            f"• Модель: {settings['model']} {'✏️' if edit_mode else ''}\n"
            f"• Температура: {settings['temperature']} {'✏️' if edit_mode else ''}\n"
            f"• Макс. токенов: {settings['max_tokens']} {'✏️' if edit_mode else ''}"
        )
        
        builder = InlineKeyboardBuilder()
        
        if edit_mode:
            # Единый стиль кнопок с иконкой карандаша
            builder.button(text="✏️ Модель", callback_data="edit_ai_model")
            builder.button(text="✏️ Температура", callback_data="edit_ai_temp")
            builder.button(text="✏️ Токены", callback_data="edit_ai_tokens")
            builder.button(
                text=f"{'🔴 Выключить' if settings['enabled'] else '🟢 Включить'} ИИ",
                callback_data="toggle_ai_enabled"
            )

            # Группировка кнопок как в основных настройках
            builder.button(text="💾 Сохранить", callback_data="save_ai_settings")
            builder.button(text="❌ Отмена", callback_data="cancel_ai_edit")
            
            # Аналогичная структура расположения
            builder.adjust(2, 1)  # 2 в первом ряду, 1 во втором
        else:
            # Стандартные кнопки управления
            builder.button(
                text=f"{theme['primary']} Редактировать", 
                callback_data="edit_ai_settings"
            )
            builder.button(
                text=f"{theme['text']} Назад", 
                callback_data="settings"
            )
            builder.adjust(2)  # обе кнопки в одном ряду
        
        return text, builder.as_markup()

    async def ai_model_selector(self, user_id: int) -> InlineKeyboardMarkup:
        """Клавиатура выбора модели AI"""
        current_model = self.config.YAGPT_MODEL
        if user_id in self.user_editing_states:
            current_model = self.user_editing_states[user_id].get('model', current_model)
        
        builder = InlineKeyboardBuilder()
        for model in ['yandexgpt-lite', 'yandexgpt-pro']:
            builder.button(
                text=f"{'✅ ' if model == current_model else ''}{model}",
                callback_data=f"set_ai_model:{model}"
            )
        builder.button(text="◀️ Назад", callback_data="edit_ai_settings")
        builder.adjust(1, 1)
        return builder.as_markup()

    async def ai_temp_selector(self, user_id: int) -> InlineKeyboardMarkup:
        """Клавиатура выбора температуры"""
        current_temp = self.config.YAGPT_TEMPERATURE
        if user_id in self.user_editing_states:
            current_temp = self.user_editing_states[user_id].get('temperature', current_temp)
        
        builder = InlineKeyboardBuilder()
        for temp in [0.1, 0.3, 0.5, 0.7, 0.9]:
            builder.button(
                text=f"{'✅ ' if abs(temp - current_temp) < 0.01 else ''}{temp}",
                callback_data=f"set_ai_temp:{temp}"
            )
        builder.button(text="🔢 Вручную", callback_data="set_ai_temp_custom")
        builder.button(text="◀️ Назад", callback_data="edit_ai_settings")
        builder.adjust(2, 2, 2, 1)
        return builder.as_markup()

    async def ai_tokens_selector(self, user_id: int) -> InlineKeyboardMarkup:
        """Клавиатура выбора токенов"""
        current_tokens = self.config.YAGPT_MAX_TOKENS
        if user_id in self.user_editing_states:
            current_tokens = self.user_editing_states[user_id].get('max_tokens', current_tokens)
        
        builder = InlineKeyboardBuilder()
        for tokens in [1000, 2000, 3000, 4000, 5000]:
            builder.button(
                text=f"{'✅ ' if tokens == current_tokens else ''}{tokens}",
                callback_data=f"set_ai_tokens:{tokens}"
            )
        builder.button(text="🔢 Вручную", callback_data="set_ai_tokens_custom")
        builder.button(text="◀️ Назад", callback_data="edit_ai_settings")
        builder.adjust(2, 2, 2, 1)
        return builder.as_markup()

    async def start_ai_edit(self, user_id: int):
        """Начинает редактирование настроек AI для пользователя"""
        self.user_editing_states[user_id] = {
            'enabled': self.config.ENABLE_YAGPT,
            'model': self.config.YAGPT_MODEL,
            'temperature': self.config.YAGPT_TEMPERATURE,
            'max_tokens': self.config.YAGPT_MAX_TOKENS
        }

    async def update_ai_setting(self, user_id: int, key: str, value: Any):
        """Обновляет временную настройку"""
        if key == 'enabled':  # Специальная обработка для переключения
            if user_id in self.user_editing_states:
                self.user_editing_states[user_id]['enabled'] = not self.user_editing_states[user_id]['enabled']
        elif user_id in self.user_editing_states:
            self.user_editing_states[user_id][key] = value

    async def save_ai_settings(self, user_id: int) -> Dict[str, Any]:
        """Сохраняет настройки и возвращает изменения"""
        if user_id not in self.user_editing_states:
            return {}
        
        changes = {}
        settings = self.user_editing_states.pop(user_id)
        
        if settings['enabled'] != self.config.ENABLE_YAGPT:
            changes['ENABLE_YAGPT'] = settings['enabled']

        if settings['model'] != self.config.YAGPT_MODEL:
            changes['YAGPT_MODEL'] = settings['model']
        
        if abs(settings['temperature'] - self.config.YAGPT_TEMPERATURE) > 0.01:
            changes['YAGPT_TEMPERATURE'] = settings['temperature']
        
        if settings['max_tokens'] != self.config.YAGPT_MAX_TOKENS:
            changes['YAGPT_MAX_TOKENS'] = settings['max_tokens']
        
        return changes

    async def cancel_ai_edit(self, user_id: int):
        """Отменяет редактирование настроек AI"""
        if user_id in self.user_editing_states:
            self.user_editing_states.pop(user_id)
    
    async def rss_settings_view(self, feeds: list, edit_mode: bool = False) -> tuple:
        """Интерактивный интерфейс управления RSS-лентами с режимом редактирования"""
        # Обработка случая, когда нет RSS-лент
        if not feeds:
            text = "📡 <b>Нет RSS-лент</b>\n\nИспользуйте кнопку ниже, чтобы добавить новую ленту"
            builder = InlineKeyboardBuilder()
            
            if edit_mode:
                builder.button(text="➕ Добавить ленту", callback_data="rss_add_start")
                builder.button(text="◀️ Назад", callback_data="settings")
                builder.adjust(1)
            else:
                builder.button(text="➕ Добавить ленту", callback_data="rss_add_start")
                builder.button(text="◀️ Назад", callback_data="settings")
                builder.adjust(1)
            
            keyboard = builder.as_markup()
            keyboard.inline_message_id = f"rss_{int(time.time())}"  # Уникальный ID
            return text, keyboard
        
        # Режим редактирования
        if edit_mode:
            text = "📡 <b>Редактирование RSS-лент</b>\n\n"
            for i, feed in enumerate(feeds):
                status = "🟢" if feed.get('active', True) else "🔴"
                error_icon = f" ❗️ {feed.get('error_count', 0)}" if feed.get('error_count', 0) > 0 else ""
                # Обрезаем длинные URL для отображения
                url_display = feed['url']
                if len(url_display) > 50:
                    url_display = url_display[:25] + "..." + url_display[-25:]
                text += f"{i+1}. {status} {url_display}{error_icon}\n"
            
            builder = InlineKeyboardBuilder()
            
            # Кнопки действий для каждой ленты
            for i, feed in enumerate(feeds):
                action = "disable" if feed.get('active', True) else "enable"
                builder.button(
                    text=f"{'⏸' if action == 'disable' else '▶️'} Лента {i+1}",
                    callback_data=f"rss_toggle_{i}_{action}"
                )
                builder.button(
                    text=f"❌ Удалить {i+1}",
                    callback_data=f"rss_remove_{i}"
                )
            
            # Общие действия
            builder.button(text="➕ Добавить ленту", callback_data="rss_add_start")
            builder.button(text="💾 Сохранить", callback_data="save_rss_settings")
            builder.button(text="❌ Отмена", callback_data="rss_settings")
            
            # Группировка кнопок: 2 кнопки на ленту, затем общие кнопки
            builder.adjust(2, *[2 for _ in range(len(feeds))], 1, 1)
        # Обычный режим просмотра
        else:
            text = "📡 <b>Текущие RSS-ленты</b>\n\n"
            for i, feed in enumerate(feeds):
                status = '🟢' if feed.get('active', True) else '🔴'
                error_icon = f" ❗️ {feed.get('error_count', 0)}" if feed.get('error_count', 0) > 0 else ""
                last_check = f" 📅 {feed.get('last_check', 'никогда')}" if feed.get('last_check') else ""
                # Обрезаем длинные URL для отображения
                url_display = feed['url']
                if len(url_display) > 50:
                    url_display = url_display[:25] + "..." + url_display[-25:]
                text += f"{i+1}. {status} {url_display}{error_icon}{last_check}\n"
            
            builder = InlineKeyboardBuilder()
            builder.button(text="✏️ Редактировать", callback_data="edit_rss_settings")
            builder.button(text="🔄 Обновить статус", callback_data="rss_refresh")
            builder.button(text="◀️ Назад", callback_data="settings")
            builder.adjust(2, 1)  # Редактировать и Обновить в одной строке, Назад отдельно
        
        keyboard = builder.as_markup()
        keyboard.inline_message_id = f"rss_{int(time.time())}"  # Уникальный ID
        return text, keyboard
    
    async def rss_add_dialog(self) -> InlineKeyboardMarkup:
        """Диалог добавления RSS"""
        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Отмена", callback_data="rss_settings")
        return builder.as_markup()
    
    async def rss_remove_selector(self, feeds: list) -> InlineKeyboardMarkup:
        """Выбор ленты для удаления"""
        builder = InlineKeyboardBuilder()
        
        for i in range(len(feeds)):
            builder.button(
                text=f"❌ Удалить {i+1}",
                callback_data=f"rss_remove_{i}"
            )
        
        builder.button(text="◀️ Назад", callback_data="rss_settings")
        builder.adjust(2, 2, 1)
        return builder.as_markup()