import logging
import json
import re
import html
import aiohttp
import asyncio
from typing import Dict, Optional

logger = logging.getLogger('AsyncYandexGPT')

class AsyncYandexGPT:
    def __init__(self, config, session: aiohttp.ClientSession):
        self.config = config
        self.session = session
        
        # Проверяем состояние сессии при инициализации
        session_ok = not session.closed if session else False
        self.active = bool(config.YANDEX_API_KEY) and config.ENABLE_YAGPT and session_ok
        self.last_error = None
        
        # Инициализация статистики
        self.stats = {
            'yagpt_used': 0,
            'yagpt_errors': 0,
            'token_usage': 0
        }

        # Счетчики ошибок для автоотключения
        self.error_count = 0
        self.consecutive_errors = 0
        self.max_consecutive_errors = 3  # Максимум ошибок перед автоотключением

        # Корректные URI для моделей (исправлено для Pro)
        self.MODEL_URIS = {
            'lite': f"gpt://{config.YANDEX_FOLDER_ID}/yandexgpt-lite/latest",
            'pro': f"gpt://{config.YANDEX_FOLDER_ID}/yandexgpt/latest",  # Исправлено для Pro
            'yandexgpt': f"gpt://{config.YANDEX_FOLDER_ID}/yandexgpt/latest",
        }

        self.headers = {
            "Authorization": f"Api-Key {config.YANDEX_API_KEY}",
            "x-folder-id": config.YANDEX_FOLDER_ID,
            "Content-Type": "application/json"
        }
        logger.info(f"YandexGPT initialized. Active: {self.active}, Model: {config.YAGPT_MODEL}")

    def is_available(self) -> bool:
        """Проверяет, доступен ли сервис в текущий момент"""
        if not self.active:
            return False
            
        # Критическая проверка: сессия закрыта?
        if self.session is None or self.session.closed:
            logger.warning("Session is closed, disabling YandexGPT")
            self.active = False
            return False
            
        # Более строгие ограничения для Pro-модели
        if self.config.YAGPT_MODEL == 'pro':
            return self.error_count < 3 and self.consecutive_errors < 2
            
        return self.error_count < self.config.YAGPT_ERROR_THRESHOLD

    def _sanitize_prompt_input(self, text: str) -> str:
        """Экранирует специальные символы и предотвращает инъекции в промпт"""
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
        Улучшает заголовок и описание с помощью Yandex GPT
        Возвращает словарь с улучшенными title и description или None при ошибке
        """
        if not self.active or not self.is_available():
            return None

        try:
            # Проверка состояния сессии перед использованием
            if self.session.closed:
                logger.error("Session is closed, cannot make request")
                self.active = False
                return None

            # Подсчет токенов (простая оценка)
            tokens = len(title.split()) + len(description.split())

            # Проверка на превышение лимита токенов
            max_tokens = min(
                self.config.YAGPT_MAX_TOKENS,
                8000 if self.config.YAGPT_MODEL == 'pro' else 2000
            )
            
            if tokens > max_tokens * 0.8:  # Оставляем запас
                logger.warning(f"Content too long: {tokens}/{max_tokens} tokens")
                return None

            # Формирование промпта
            prompt = self.config.YAGPT_PROMPT.format(
                title=self._sanitize_prompt_input(title),
                description=self._sanitize_prompt_input(description)
            )

            # Получаем корректный URI модели
            model_uri = self.MODEL_URIS.get(
                self.config.YAGPT_MODEL,
                self.MODEL_URIS['lite']  # Fallback
            )

            # Подготовка данных для запроса
            request_data = {
                "modelUri": model_uri,
                "completionOptions": {
                    "stream": False,
                    "temperature": self.config.YAGPT_TEMPERATURE,
                    "maxTokens": max_tokens
                },
                "messages": [
                    {
                        "role": "user",
                        "text": prompt
                    }
                ]
            }

            # Логирование для отладки
            logger.debug(f"YandexGPT request to {self.config.YANDEX_API_ENDPOINT}")
            logger.debug(f"Model: {self.config.YAGPT_MODEL}, URI: {model_uri}")
            logger.debug(f"Prompt: {prompt[:200]}...")

            # Отправка запроса
            async with self.session.post(
                self.config.YANDEX_API_ENDPOINT,
                headers={
                    "Authorization": f"Api-Key {self.config.YANDEX_API_KEY}",
                    "Content-Type": "application/json",
                    "x-folder-id": self.config.YANDEX_FOLDER_ID
                },
                json=request_data,
                timeout=aiohttp.ClientTimeout(total=60 if self.config.YAGPT_MODEL == 'pro' else 30)
            ) as response:

                response_text = await response.text()
                
                if response.status != 200:
                    self._handle_error(response.status, response_text, request_data)
                    return None

                try:
                    data = await response.json()
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON response: {response_text[:500]}")
                    self._handle_error(500, "Invalid JSON", request_data)
                    return None

                # Логирование сырого ответа
                logger.debug(f"Raw response: {json.dumps(data, ensure_ascii=False)[:500]}...")

                # Парсинг результата
                parsed_response = self.parse_response(data)
                if parsed_response:
                    self.stats['yagpt_used'] += 1
                    self.consecutive_errors = 0  # Сброс счетчика ошибок
                    
                    # Проверка качества ответа
                    if self.is_low_quality_response(parsed_response['description']):
                        logger.warning("Low quality response detected")
                        self.stats['yagpt_errors'] += 1
                        return None
                        
                    return parsed_response
                
                logger.warning("Failed to parse YandexGPT response")
                self._handle_error(500, "Parsing failed", request_data)
                return None

        except asyncio.TimeoutError:
            logger.error("Yandex GPT request timeout")
            self._handle_error(408, "Timeout", {})
            return None
        except RuntimeError as e:
            if "Session is closed" in str(e):
                logger.critical("Session closed during request! Disabling service.")
                self.active = False
                self._handle_error(500, "Session closed", {})
            else:
                logger.error(f"Runtime error in YandexGPT: {str(e)}")
                self._handle_error(500, str(e), {})
            return None
        except aiohttp.ClientConnectionError as e:
            logger.error(f"Connection error: {str(e)}")
            self._handle_error(503, "Connection error", {})
            return None
        except Exception as e:
            logger.error(f"Yandex GPT enhancement error: {str(e)}", exc_info=True)
            self._handle_error(500, str(e), {})
            return None

    def _handle_error(self, status: int, error: str, request_data: dict):
        """Обрабатывает ошибки и обновляет счетчики"""
        self.error_count += 1
        self.consecutive_errors += 1
        self.stats['yagpt_errors'] += 1
        
        logger.error(f"Yandex GPT API error: {status} - {error[:500]}")
        logger.debug(f"Request body: {json.dumps(request_data, ensure_ascii=False)[:500]}...")
        
        # Автоотключение при частых ошибках
        if self.consecutive_errors >= self.max_consecutive_errors:
            logger.warning("Disabling YandexGPT due to consecutive errors")
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

    def parse_response(self, data: Dict) -> Optional[Dict]:
        try:
            if not data.get('result') or not data['result'].get('alternatives'):
                logger.warning("No alternatives in YandexGPT response")
                return None

            text = data['result']['alternatives'][0]['message']['text']
            logger.debug(f"Response text: {text[:200]}...")

            # Попытка прямого JSON парсинга
            try:
                start_idx = text.find('{')
                end_idx = text.rfind('}')
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    json_str = text[start_idx:end_idx+1]
                    result = json.loads(json_str)
                    if isinstance(result, dict) and 'title' in result and 'description' in result:
                        return {
                            'title': self._sanitize_text(result['title'])[:self.config.MAX_TITLE_LENGTH],
                            'description': self._sanitize_text(result['description'])[:self.config.MAX_DESC_LENGTH]
                        }
            except (ValueError, json.JSONDecodeError, AttributeError):
                pass

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

        except Exception as e:
            logger.error(f"YandexGPT parsing error: {str(e)}", exc_info=True)
            return None

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Sanitizes text for Telegram HTML parsing"""
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