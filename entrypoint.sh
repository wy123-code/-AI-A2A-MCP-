#!/bin/bash
set -e

case "${SERVICE:-web}" in
  web)
    echo "Starting FastAPI web server..."
    exec uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log
    ;;
  worker)
    echo "Starting Celery worker..."
    exec celery -A celery_app worker -l info -P gevent
    ;;
  beat)
    echo "Starting Celery beat..."
    exec celery -A celery_app beat -l info
    ;;
  *)
    echo "Unknown SERVICE: ${SERVICE}, defaulting to web"
    exec uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log
    ;;
esac
