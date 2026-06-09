# Imagen de la aplicación de procesamiento de solicitudes TUMIPAY.
FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias primero (mejor cacheo de capas).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código y datos de ejemplo.
COPY src/ ./src/
COPY data/ ./data/

# Ejecuta el flujo sobre el CSV de ejemplo. La conexión a la BD la define
# DATABASE_URL (inyectada por docker-compose, apuntando a PostgreSQL).
CMD ["python", "src/procesar.py"]
