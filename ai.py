import logging
import json
import re
import html
from typing import Dict, Optional, Union
from openai import AsyncOpenAI, OpenAIError
import aiohttp
import asyncio

logger = logging.getLogger('AsyncAI')

class AsyncAI:
    def __init__(self, config, session: aiohttp.ClientSession = None):
        self.config = config
        self.session = session
        self.active = bool(config.AI_API_KEY) and config.ENABLE_AI
        self.last_error = None
        
        # Инициализация клиента в зависимости от типа провайдера
        if config.AI_PROVIDER_TYPE == "openai":
            self.client = AsyncOpenAI(
                api_key=config.AI_API_KEY,
                base_url=config.AI_BASE_URL,
            )
        else:
            # Для других провайдеров используем aiohttp
            self.client = None
        
        # Инициализация статистики
        self.stats = {
            'ai_used': 0,
            'ai_errors': 0,
            'token_usage': 0
        }

        # Счетчики ошибок для автоотключения
        self.error_count = 0
        self.consecutive_errors = 0
        self.max_consecutive_errors = config.AI_ERROR_THRESHOLD

        logger.info(f"AI Provider initialized. Active: {self.active}, Type: {config.AI_PROVIDER_TYPE}, Model: {config.AI_MODEL}")

    def is_available(self) -> bool:
        """Проверяет, доступен ли сервис в текущий момент"""
        if not self.active:
            return False
        return self.error_count < self.config.AI_ERROR_THRESHOLD

    def _sanitize_prompt_input(self, text: str) -> str:
        """Экранирует специальные символы"""
        if not isinstance(text, str):
            return ""

        sanitized = html.escape(text)
        replacements = {
            '{': '{{',
            '}': '}}',
            '[': '【',
            ']': '】',
            '(': '（',
            ')': '）',
            '"': '\\"',
            "'": "\\'",
            '\n': ' ',
            '\r': ' ',
            '\t': ' '
        }

        for char, replacement in replacements.items():
            sanitized = sanitized.replace(char, replacement)

        sanitized = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', sanitized)
        return sanitized[:5000]

    async def enhance(self, title: str, description: str) -> Optional[Dict]:
        """
        Улучшает заголовок и описание с помощью AI
        Возвращает словарь с улучшенными title и description или None при ошибке
        """
        if not self.active or not self.is_available():
            return None

        try:
            # Формирование промпта
            prompt = self.config.AI_PROMT.format(
                title=self._sanitize_prompt_input(title),
                description=self._sanitize_prompt_input(description)
            )

            # Отправка запроса в зависимости от типа провайдера
            if self.config.AI_PROVIDER_TYPE == "openai":
                response = await self.client.chat.completions.create(
                    model=self.config.AI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.config.AI_MAX_TOKENS,
                    temperature=self.config.AI_TEMPERATURE,
                )
                result_text = response.choices[0].message.content
            else:
                # Для других провайдеров
                result_text = await self._make_custom_api_request(prompt)

            # Парсим результат
            parsed_response = self.parse_response(result_text)
            if parsed_response:
                self.stats['ai_used'] += 1
                self.consecutive_errors = 0
                
                # Проверка качества ответа
                if self.is_low_quality_response(parsed_response['description']):
                    logger.warning("Low quality response detected")
                    self.stats['ai_errors'] += 1
                    return None
                    
                return parsed_response

            logger.warning("Failed to parse AI response")
            self._handle_error("Parsing failed", {})
            return None

        except Exception as e:
            logger.error(f"AI enhancement error: {str(e)}", exc_info=True)
            self._handle_error(str(e), {})
            return None

    async def _make_custom_api_request(self, prompt: str) -> str:
        """Выполняет запрос к кастомному API"""
        # Реализация зависит от конкретного провайдера
        # Здесь можно добавить логику для разных API
        return ""

    def _handle_error(self, error: str, request_data: dict):
        """Обрабатывает ошибки и обновляет счетчики"""
        self.error_count += 1
        self.consecutive_errors += 1
        self.stats['ai_errors'] += 1
        
        logger.error(f"AI API error: {error}")
        
        # Автоотключение при частых ошибках
        if self.consecutive_errors >= self.max_consecutive_errors:
            logger.warning("Disabling AI due to consecutive errors")
            self.active = False

    def is_low_quality_response(self, text: str) -> bool:
        """Определяет низкокачественный ответ ИИ"""
        if not text:
            return True

        quality_indicators = [
            "в интернете есть много сайтов",
            "посмотрите, что нашлось в поиске",
            "дополнительные материалы:",
            "смотрите также:",
            "читайте далее",
            "читайте также",
            "рекомендуем прочитать",
            "подробнее на сайте",
            "другие источники:",
            "больше информации можно найти",
            r"\[.*\]\(https?://[^\)]+\)"  # Markdown ссылки
        ]

        text_lower = text.lower()
        return any(re.search(phrase, text_lower) for phrase in quality_indicators)

    def parse_response(self, data: Union[Dict, str]) -> Optional[Dict]:
        """Парсит ответ от API"""
        try:
            # Если data - это строка, пытаемся распарсить как JSON
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    # Если это не JSON, обрабатываем как текст
                    return self._parse_text_response(data)
            
            # Обработка JSON-ответа
            if isinstance(data, dict):
                # Прямой JSON парсинг
                if 'title' in data and 'description' in data:
                    return {
                        'title': self._sanitize_text(data['title'])[:self.config.MAX_TITLE_LENGTH],
                        'description': self._sanitize_text(data['description'])[:self.config.MAX_DESC_LENGTH]
                    }
                
                # Попытка извлечения из структур разных провайдеров
                text = self._extract_text_from_response(data)
                if text:
                    return self._parse_text_response(text)
            
            return self._parse_text_response(str(data))
            
        except Exception as e:
            logger.error(f"AI parsing error: {str(e)}", exc_info=True)
            return None

    def _extract_text_from_response(self, data: Dict) -> Optional[str]:
        """Извлекает текст из ответов разных провайдеров"""
        # Для OpenAI-совместимых провайдеров
        if 'choices' in data and len(data['choices']) > 0:
            choice = data['choices'][0]
            if 'message' in choice and 'content' in choice['message']:
                return choice['message']['content']
        
        return None

    def _parse_text_response(self, text: str) -> Optional[Dict]:
        """Парсит текстовый ответ"""
        # Расширенные шаблоны для извлечения данных
        patterns = [
            r'(?i)title["\']?:\s*["\'](.+?)["\']',
            r'(?i)заголовок["\']?:\s*["\'](.+?)["\']',
            r'(?i)(?:title|заголовок)[\s:]*["\']?(.+?)["\']?(?:\n|$|\.)',
            r'(?i)(?:description|описание)[\s:]*["\']?(.+?)["\']?(?:\n|$|\.)',
            r'{"title"\s*:\s*"([^"]+)"[^}]*"description"\s*:\s*"([^"]+)"}',
            r'<title>(.+?)</title>\s*<description>(.+?)</description>',
            r'(?i)(?:заголовок|title):?\s*([^\n]+)\n+(?:описание|description):?\s*([^\n]+)'
        ]

        title_match = None
        desc_match = None

        # Поиск заголовка
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match and match.lastindex >= 1:
                title_candidate = match.group(1).strip()
                if len(title_candidate) > 5:
                    title_match = title_candidate
                    break

        # Поиск описания
        if title_match:
            for pattern in patterns:
                match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                if match and match.lastindex >= 2:
                    desc_candidate = match.group(2).strip()
                    if len(desc_candidate) > 10:
                        desc_match = desc_candidate
                        break

        # Fallback стратегии
        if not title_match or not desc_match:
            parts = re.split(r'\n\n|\n-|\n•', text, maxsplit=1)
            if len(parts) >= 2:
                title_match = parts[0].strip()
                desc_match = parts[1].strip()
            else:
                sentences = re.split(r'[.!?]\s+', text)
                if len(sentences) > 1:
                    title_match = sentences[0]
                    desc_match = ' '.join(sentences[1:3])[:500]
                else:
                    title_match = text[:100]
                    desc_match = text[100:500] if len(text) > 100 else ""

        return {
            'title': self._sanitize_text(title_match)[:self.config.MAX_TITLE_LENGTH],
            'description': self._sanitize_text(desc_match)[:self.config.MAX_DESC_LENGTH]
        }

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Sanitizes text for Telegram HTML parsing while preserving emoji and Unicode"""
        if not text:
            return ""
        sanitized = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', str(text))
        return (
            sanitized
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", "&apos;")
        )