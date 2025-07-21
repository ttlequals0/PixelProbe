#!/bin/bash

# Run PixelProbe with Modern UI locally

echo "Starting PixelProbe with Modern UI..."
echo "Using database: ${DATABASE_PATH:-./instance/media_checker.db}"
echo ""
echo "Access the application at: http://localhost:5001"
echo ""

# Set environment variables
export USE_MODERN_UI=true
export DATABASE_URL="sqlite:///${DATABASE_PATH:-./instance/media_checker.db}"
export FLASK_APP=app.py
export FLASK_ENV=development

# Create static directories if they don't exist
mkdir -p static/css
mkdir -p static/js
mkdir -p static/images

# Run the application
python app.py