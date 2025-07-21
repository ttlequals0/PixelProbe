#!/bin/bash

# Setup and run PixelProbe locally with modern UI

echo "=== PixelProbe Local Development Setup ==="
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "Virtual environment created."
else
    echo "Virtual environment already exists."
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install/upgrade dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Set environment variables
export USE_MODERN_UI=true
export DATABASE_URL="sqlite:///${DATABASE_PATH:-./instance/media_checker.db}"
export FLASK_APP=app.py
export FLASK_ENV=development
export FLASK_DEBUG=1
export SECRET_KEY="development-secret-key-change-in-production"

# Create necessary directories
echo "Creating static directories..."
mkdir -p static/css
mkdir -p static/js
mkdir -p static/images

# Display database info
echo ""
echo "=== Configuration ==="
echo "Database: ${DATABASE_PATH:-./instance/media_checker.db}"
echo "UI Mode: Modern UI"
echo "Server: http://localhost:5001"
echo ""

# Check if database exists
if [ -f "${DATABASE_PATH:-./instance/media_checker.db}" ]; then
    echo "✓ Database file found"
    # Get file size
    DB_SIZE=$(ls -lh "${DATABASE_PATH:-./instance/media_checker.db}" | awk '{print $5}')
    echo "  Database size: $DB_SIZE"
else
    echo "✗ WARNING: Database file not found at ${DATABASE_PATH:-./instance/media_checker.db}"
    echo "  Please ensure the database file exists at this location."
fi

echo ""
echo "=== Starting PixelProbe ==="
echo "Press Ctrl+C to stop the server"
echo ""

# Run the Flask application
python app.py