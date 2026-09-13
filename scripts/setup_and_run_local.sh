#!/bin/bash
set -euo pipefail

if [ ! -d "venv" ]; then
    python3.12 -m venv venv
fi
source venv/bin/activate
python_version=$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$python_version" != "3.12" ]; then
    echo "venv must use Python 3.12. Move the existing venv aside, then create a new one with python3.12 -m venv venv."
    exit 1
fi
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm ci
npm run build

for variable in SECRET_KEY POSTGRES_HOST POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
    if [ -z "${!variable:-}" ]; then
        echo "$variable must be set before starting PixelProbe."
        exit 1
    fi
done

export FLASK_APP=app.py
export FLASK_ENV=development
export FLASK_DEBUG=1
# Local HTTP only. Production must keep SESSION_COOKIE_SECURE enabled.
export SESSION_COOKIE_SECURE=false

echo "Starting PixelProbe at http://127.0.0.1:5000"
flask run --host 127.0.0.1 --port 5000
