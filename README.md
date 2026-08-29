# Agentic Agriculture MVP

Secure Django bootstrap for the Agentic Agriculture MVP. This first delivery provides a
database-free demonstration login, a protected home page, PT-BR/English UI, operational
probes, and one production image ready for Google Cloud Run.

## Scope

This repository currently contains only the application foundation:

- Python 3.12 and Django 5.2;
- one demonstration account configured entirely through environment variables;
- Django signed-cookie sessions, with no session table or database dependency;
- protected `/`, login, POST-only logout, language selection, and static assets;
- public liveness and readiness endpoints;
- Gunicorn + WhiteNoise production runtime;
- pytest, Ruff, Docker/Compose, and GitHub Actions.

Agricultural agents, domain workflows, persistence, integrations, queues, and uploads are
deliberately outside this delivery and belong to later work.

## Quick start with Docker

Requirements: Docker with Compose.

1. Copy `.env.example` to `.env`.
2. Replace `DJANGO_SECRET_KEY` with a long random value.
3. Generate a password hash (the password itself is never placed in `.env`):

   ```bash
   docker run --rm python:3.12-slim sh -c \
     "pip install 'Django>=5.2,<5.3' >/dev/null && python -c \"from django.conf import settings; settings.configure(); from django.contrib.auth.hashers import make_password; print(make_password('choose-a-password'))\""
   ```

4. Put that entire output in `DEMO_PASSWORD_HASH`. Keep it inside single quotes because
   Django hashes contain `$` characters.
5. Start the service:

   ```bash
   docker compose up --build
   ```

Open <http://localhost:8080/login/> and use `DEMO_USERNAME` plus the password chosen in
step 3. The Compose health check calls `/readyz`, so the container remains unhealthy until
the demonstration credentials are valid.

## Local development

Python 3.12 is required. On PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Django does not automatically read `.env` files. Export the values in your shell or use a
local environment loader of your choice. At minimum, set `DEMO_USERNAME` and a valid
`DEMO_PASSWORD_HASH`; generate the latter after installing dependencies:

```powershell
python -c "from django.conf import settings; settings.configure(); from django.contrib.auth.hashers import make_password; print(make_password('choose-a-password'))"
```

Then run:

```powershell
python manage.py runserver
```

Compiled PT-BR messages are included in the repository. After editing `locale/**/*.po`,
run `python manage.py compilemessages` with GNU gettext installed. The Docker build does
this automatically.

## Configuration

| Variable | Required | Purpose |
| --- | --- | --- |
| `APP_ENV` | No | `development` by default; `production` enables secure defaults. |
| `DJANGO_SECRET_KEY` | Production | Signs cookies and CSRF data; keep stable across replicas. |
| `DJANGO_ALLOWED_HOSTS` | Production | Comma-separated hostnames, such as `service-xyz.run.app`. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | For custom origins | Comma-separated HTTPS origins including the scheme. |
| `DJANGO_DEBUG` | No | Defaults to false in production. Never enable in production. |
| `PRODUCT_NAME` | No | Product label displayed in the UI. |
| `DEMO_USERNAME` | Yes for readiness | Username for the single demonstration account. |
| `DEMO_PASSWORD_HASH` | Yes for readiness | Encoded Django password hash, never a plain password. |
| `DJANGO_TIME_ZONE` | No | Defaults to `America/Sao_Paulo`. |
| `DJANGO_SESSION_COOKIE_AGE` | No | Maximum session age in seconds; defaults to 8 hours. |
| `DJANGO_COOKIE_SECURE` | No | Defaults to true in production. |
| `DJANGO_SECURE_SSL_REDIRECT` | No | Defaults to true in production. |
| `DJANGO_SECURE_HSTS_SECONDS` | No | Defaults to one year in production and zero locally. |
| `PORT` | No | HTTP port; defaults to `8080`, as expected by Cloud Run. |
| `WEB_CONCURRENCY` | No | Gunicorn workers; defaults to 2. |
| `GUNICORN_THREADS` | No | Threads per worker; defaults to 4. |

Production startup fails if `DJANGO_SECRET_KEY` or `DJANGO_ALLOWED_HOSTS` is absent. The
readiness probe returns `503` if the demonstration username or password hash is absent or
malformed.

## Routes

| Method | Route | Description |
| --- | --- | --- |
| `GET` | `/` | Protected home page. |
| `GET`, `POST` | `/login/` | Demonstration login; POST is CSRF-protected. |
| `POST` | `/logout/` | Clears the signed session. |
| `POST` | `/i18n/setlang/` | Persists PT-BR or English in Django's language cookie. |
| `GET` | `/healthz` | Cheap liveness response, independent of auth and persistence. |
| `GET` | `/readyz` | Validates the minimum demonstration credential configuration. |

## Quality checks

```bash
ruff check .
ruff format --check .
pytest
python manage.py check
docker build -t agentic-agriculture-mvp .
```

Tests intentionally run without `django_db`: there are no models, migrations, SQLite file,
or session table in this bootstrap.

## Cloud Run

Build and deploy the same `Dockerfile`. Configure at least these runtime variables in the
Cloud Run service:

```text
APP_ENV=production
DJANGO_SECRET_KEY=<managed secret>
DJANGO_ALLOWED_HOSTS=<service host>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<service host>
DEMO_USERNAME=<demo user>
DEMO_PASSWORD_HASH=<encoded Django hash>
PRODUCT_NAME=Agentic Agriculture
```

The image runs as a non-root user, listens on `0.0.0.0:$PORT`, serves static assets through
WhiteNoise, and understands Cloud Run's `X-Forwarded-Proto` header. Prefer your cloud secret
manager for the secret key and password hash rather than plain deployment arguments.

## Security model and limitations

Signed-cookie sessions are integrity-protected but **not encrypted**. This application stores
only an authentication-version HMAC in the session cookie—never the username, password, or
configured password hash. Changing the username, password hash, or Django secret key
invalidates existing sessions.

This is deliberately a single-account demonstration gate, not a full identity system. It has
no centralized session revocation, user administration, audit history, or distributed login
rate limiting. Keep the session lifetime short, serve only over HTTPS, protect environment
secrets, and replace this mechanism with managed identity before opening the product to real
users.
