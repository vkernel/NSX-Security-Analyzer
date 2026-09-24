FROM python:3.12-slim
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/vkernel/NSX-Security-Analyzer"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/webapp
COPY webapp/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY LICENSE NOTICE /app/
COPY webapp/ /app/webapp/
RUN DJANGO_SECRET_KEY=build-only-static-files python manage.py collectstatic --noinput \
    && groupadd --gid 10001 nsx && useradd --uid 10001 --gid nsx --no-create-home nsx
ARG APP_REVISION=source
ARG APP_BUILD_DATE=unknown
LABEL org.opencontainers.image.version="0.1.0"
LABEL org.opencontainers.image.revision=$APP_REVISION
LABEL org.opencontainers.image.created=$APP_BUILD_DATE
RUN python -c 'import json, os; from pathlib import Path; Path("inventory/_build.json").write_text(json.dumps({"revision": os.environ["APP_REVISION"], "built_at": os.environ["APP_BUILD_DATE"]}))'
USER nsx
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "180", "--access-logfile", "-"]
