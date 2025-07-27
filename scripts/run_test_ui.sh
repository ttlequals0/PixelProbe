#!/bin/bash

# Run PixelProbe with test database

source venv/bin/activate

export USE_MODERN_UI=true
export DATABASE_URL="sqlite:///$PWD/test_media_checker.db"
export FLASK_APP=app.py
export FLASK_ENV=development
export FLASK_DEBUG=1
export SECRET_KEY="development-secret-key"

echo "=== PixelProbe Test Server ==="
echo "Database: test_media_checker.db (with sample data)"
echo "UI: Modern UI"
echo "URL: http://localhost:5001"
echo ""
echo "Press Ctrl+C to stop"
echo ""

python app.py
