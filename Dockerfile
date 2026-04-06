FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends freetds-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/
COPY config/ config/

RUN pip install --no-cache-dir \
    "sqlalchemy>=2.0" \
    "psycopg[binary]>=3.1" \
    "pymysql>=1.1" \
    "cryptography>=42.0" \
    "pymssql>=2.3" \
    "click>=8.1" \
    "questionary>=2.0" \
    "rich>=13.0" \
    "pyyaml>=6.0" \
    "jinja2>=3.1" \
    "fastapi>=0.110" \
    "uvicorn[standard]>=0.27" \
    "websockets>=12.0" \
    "markdown>=3.5"

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["python", "-m", "sqlio_cloud.cli", "web", "--host", "0.0.0.0", "--port", "8000"]
