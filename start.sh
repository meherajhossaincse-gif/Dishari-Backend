#!/bin/sh
set -e
echo "Waiting for database + running migrations..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  alembic upgrade head && break
  echo "DB not ready yet ($i), retrying in 3s"; sleep 3
done
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 ${UVICORN_EXTRA:-}
