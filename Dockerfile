FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

ENV RESLOOM_DB=/app/data/resloom.db
ENV PYTHONUNBUFFERED=1

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin resloom \
    && mkdir -p /app/data \
    && chown resloom:resloom /app/data
USER resloom

EXPOSE 8501

# The container listens on all its interfaces; docker-compose.yml publishes the port on 127.0.0.1 only.
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
