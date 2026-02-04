# Usar una imagen base de Python oficial ligera
FROM python:3.11-slim

# Evitar que Python genere archivos .pyc y buffers en stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Establecer directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema necesarias
RUN apt-get update && apt-get install -y \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar navegadores de Playwright y sus dependencias de sistema
RUN playwright install --with-deps chromium

# Copiar el resto del código
COPY . .

# Comando por defecto al iniciar el contenedor
CMD ["python", "bot.py"]
