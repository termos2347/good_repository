Telegram RSS Bot with AI Enhancement
📦 Зависимости
Основные зависимости (указаны в requirements.txt)
text
aiohttp==3.9.5        # Асинхронные HTTP-запросы
aiogram==3.3.1        # Telegram Bot API
feedparser==6.0.10    # Парсинг RSS/Atom
Pillow==10.2.0        # Генерация изображений
python-dotenv==1.0.0  # Загрузка .env конфигурации
python-Levenshtein==0.25.0  # Проверка схожести текстов
Дополнительные системные зависимости
bash
# Для Ubuntu/Debian
sudo apt-get install -y python3-dev python3-pip libfreetype6-dev libjpeg-dev libopenjp2-7-dev zlib1g-dev

# Для CentOS/RHEL
sudo yum install -y python3-devel freetype-devel libjpeg-turbo-devel openjpeg2-devel zlib-devel
⚙️ Примеры конфигураций
1. Быстрый новостной бот (максимальная частота)
env
POSTS_PER_HOUR=0                # Без ограничений
MIN_DELAY_BETWEEN_POSTS=5       # 5 сек между постами
CHECK_INTERVAL=60               # Проверка каждую минуту
DISABLE_YAGPT=true              # Без AI обработки
ENABLE_IMAGE_GENERATION=false   # Без генерации изображений
IMAGE_SOURCE=none               # Только текст
2. Премиум контент с AI (качественная обработка)
env
POSTS_PER_HOUR=6                # 6 постов в час
MIN_DELAY_BETWEEN_POSTS=300     # 5 мин между постами
CHECK_INTERVAL=600              # Проверка каждые 10 мин
DISABLE_YAGPT=false             
YAGPT_MODEL=yandexgpt-pro       # Профессиональная модель
YAGPT_TEMPERATURE=0.7           # Более креативные тексты
ENABLE_IMAGE_GENERATION=true    
IMAGE_GENERATION_WORKERS=4      # 4 потока для генерации
IMAGE_SOURCE=template           # Использовать шаблоны
3. Технический блог (баланс скорости и качества)
env
POSTS_PER_HOUR=12               # 12 постов в час
MIN_DELAY_BETWEEN_POSTS=120     # 2 мин между постами
CHECK_INTERVAL=300              # Проверка каждые 5 мин
DISABLE_YAGPT=false
YAGPT_MODEL=yandexgpt-lite      # Оптимизированная модель
YAGPT_TEMPERATURE=0.4           # Баланс креативности
MAX_TEXT_LINES=2                # Компактные заголовки
IMAGE_SOURCE=original           # Оригинальные изображения
IMAGE_FALLBACK=true             # Резервная генерация
🚀 Руководство по развертыванию
1. Локальная установка (для разработки)
bash
# Клонирование репозитория
git clone https://github.com/yourusername/telegram-rss-bot.git
cd telegram-rss-bot

# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Установка зависимостей
pip install -r requirements.txt

# Создание .env файла (скопируйте пример из .env.example)
cp .env.example .env

# Запуск бота
python main.py
2. Docker-развертывание (production)
dockerfile
# Dockerfile
FROM python:3.9-slim

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "main.py"]
bash
# Сборка и запуск
docker build -t rss-bot .
docker run -d --name rss-bot -v $(pwd)/.env:/app/.env rss-bot
3. Развертывание на сервере
bash
# Установка systemd сервиса (Ubuntu)
sudo nano /etc/systemd/system/rss-bot.service
ini
[Unit]
Description=Telegram RSS Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/path/to/bot
ExecStart=/path/to/venv/bin/python /path/to/bot/main.py
Restart=always
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
bash
# Активация сервиса
sudo systemctl daemon-reload
sudo systemctl enable rss-bot
sudo systemctl start rss-bot
4. Мониторинг
bash
# Просмотр логов
journalctl -u rss-bot -f

# Проверка состояния
sudo systemctl status rss-bot
🔧 Рекомендации по обслуживанию
Ротация логов
Добавьте в /etc/logrotate.d/rss-bot:

text
/path/to/bot/logs/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
}
Обновление
Для обновления бота:

bash
git pull origin main
sudo systemctl restart rss-bot
Резервное копирование
Критичные данные для бэкапа:

bot_state.json




.env
.gitignore
Dockerfile
README.txt
bot_controller.py
bot_state.json
config.py
docker-compose.yaml
image_config.json
image_generator.py
main.py
requirements.txt
rss_parser.py
state_manager.py
telegram_interface.py
visual_interface.py
yandex_gpt.py