FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/install/lib/python3.12/site-packages

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md manage.py ./
COPY agriculture ./agriculture
COPY config ./config
COPY core ./core
COPY geospatial ./geospatial
COPY templates ./templates
COPY locale ./locale

RUN python -m pip install --no-cache-dir --prefix=/install . \
    && python manage.py compilemessages --verbosity 0 \
    && APP_ENV=production \
       DJANGO_SECRET_KEY=build-only-static-manifest-secret \
       DJANGO_ALLOWED_HOSTS=localhost \
       python manage.py collectstatic --noinput --verbosity 0


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    PORT=8080

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --no-create-home app

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder --chown=app:app /app /app

USER app

EXPOSE 8080

CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-2} --threads ${GUNICORN_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-60} --access-logfile - --error-logfile -"]
