#!/bin/bash

# Setup test environment and create test database

echo "=== Setting up test environment ==="

# Create and activate virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Create test database
echo ""
echo "Creating test database..."
python3 create_test_database.py

# Update the run script to use test database
echo ""
echo "Updating configuration..."
cat > run_test_ui.sh << 'EOF'
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
EOF

chmod +x run_test_ui.sh

echo ""
echo "✅ Setup complete!"
echo ""
echo "To run the application with test data:"
echo "   ./run_test_ui.sh"
echo ""