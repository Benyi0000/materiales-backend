FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema requeridas para postgres y pillow
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Instalar dependencias de Python y servidores listos para producción
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn psycopg2-binary celery redis

COPY . .

# Exponer el puerto
EXPOSE 8000

# El comando por defecto, pero se sobreescribirá en docker-compose para Celery
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "config.wsgi:application"]
