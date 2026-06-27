#!/bin/sh
set -e

alembic upgrade head
exec uvicorn app.main:create_app --factory --host "${APP_HOST:-0.0.0.0}" --port "${APP_PORT:-8000}"
