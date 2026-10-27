# BhashaRadar — container for Render (or any Docker host)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BR_CLOUD=1 \
    BR_DATA_DIR=/tmp/bhasharadar

WORKDIR /srv
COPY requirements.txt requirements-cloud.txt ./
RUN pip install -r requirements-cloud.txt

COPY app ./app
COPY verify_sources.py ./

RUN useradd --create-home --uid 10001 radar
USER radar

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/healthz')"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
