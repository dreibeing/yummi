FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY yummi-server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY yummi-server/app ./app
COPY yummi-server/alembic.ini ./alembic.ini
COPY yummi-server/alembic ./alembic
COPY resolver ./resolver

ENV PORT=8000
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
