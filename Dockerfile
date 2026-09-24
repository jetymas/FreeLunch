FROM python:3.11-slim

WORKDIR /app
ENV FREELUNCH_UID=10001
ENV DATABASE_URL=/app/data/freelunch.db
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y setuptools wheel

COPY . .
RUN groupadd --gid 10001 freelunch \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin freelunch \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data
COPY deploy/migrate-data-ownership.sh /usr/local/bin/migrate-data-ownership
RUN chmod 0755 /usr/local/bin/migrate-data-ownership
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"]
USER 10001:10001
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
