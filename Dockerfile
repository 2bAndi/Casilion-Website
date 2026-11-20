# Cassilion web platform: one image for the web application and the AI worker.
# Same pattern as Tango-Kalender: the host nginx terminates TLS and proxies to this
# container on 127.0.0.1 (docs/server-setup.md).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    STATIC_ROOT=/app/staticfiles

WORKDIR /app
RUN useradd --create-home --uid 1000 app

# WeasyPrint (invoice PDFs) needs Pango and a font from the operating system.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-postgres.txt ./
RUN pip install -r requirements.txt -r requirements-postgres.txt

COPY . .
# Collect the static files into the image (var/ is a mounted volume at run time).
# The values below only let the settings load during the build; none of them is kept.
RUN DEBUG=0 SECRET_KEY=build AI_WORKER_TOKEN=build ALLOW_SANDBOX_PAYMENTS=1 CASSILION_VAR_DIR=/tmp/build-var \
        python manage.py collectstatic --noinput \
    && mkdir -p /app/var \
    && chown -R app:app /app

# A container never runs with development defaults, whatever the .env file forgets.
ENV DEBUG=0

USER app
EXPOSE 8000
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
# ASGI, because the MCP endpoints live in the same application. They are stateless, so two workers are fine.
CMD ["python", "-m", "uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
