FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY factory ./factory

USER 10001:10001
EXPOSE 8090
CMD ["uvicorn", "factory.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8090"]
