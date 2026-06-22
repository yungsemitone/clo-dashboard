FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

# Install deps first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + the committed SQLite DB (data/clo_data.db). secrets.toml and the
# raw/processed/export data dirs are excluded via .dockerignore.
COPY . .

EXPOSE 8501

# Imports are rooted at the repo top (PYTHONPATH=/app). Bind 0.0.0.0 so Fly's
# proxy can reach Streamlit on the internal port.
CMD ["streamlit", "run", "Overview.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
