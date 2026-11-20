#!/bin/sh
set -e
# Apply database migrations before the app starts
python manage.py migrate --noinput
exec "$@"
