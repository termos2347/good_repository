FROM python:3.11-slim

WORKDIR /app

# Установить системные пакеты для сборки C-зависимостей Python
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --upgrade pip

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
