# Tek asamali imaj: uygulama derleme adimi olmayan bir on yuz ve saf Python
# backend'den olusuyor, bu yuzden multi-stage'e gerek yok.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend \
    DATA_DIR=/data \
    AUTO_SEED=1

WORKDIR /app

# Bagimliliklar once: kod degisince katman yeniden kurulmaz.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/
COPY configs/ configs/
COPY models/ models/

# Disk baglanmazsa da yazilabilir bir yer olsun.
RUN mkdir -p /data

EXPOSE 8000

# $PORT barindirma saglayicisi tarafindan atanir; yoksa 8000.
CMD ["sh", "-c", "exec uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000}"]
