#!/bin/bash

# Build and run PixelProbe with Modern UI in Docker

echo "=== Building PixelProbe Modern UI Docker Image ==="
echo ""

# Stop any existing container
echo "Stopping existing container if running..."
docker-compose -f docker-compose.modern.yml down 2>/dev/null

# Build the image
echo "Building Docker image..."
docker-compose -f docker-compose.modern.yml build

# Start the container
echo ""
echo "Starting PixelProbe Modern UI..."
docker-compose -f docker-compose.modern.yml up -d

# Wait for startup
echo ""
echo "Waiting for application to start..."
sleep 5

# Check if container is running
if docker ps | grep -q pixelprobe-modern-ui; then
    echo ""
    echo "✅ PixelProbe Modern UI is running!"
    echo ""
    echo "🌐 Access the application at: http://localhost:5001"
    echo ""
    echo "📊 The application includes sample test data"
    echo ""
    echo "To view logs:"
    echo "  docker-compose -f docker-compose.modern.yml logs -f"
    echo ""
    echo "To stop:"
    echo "  docker-compose -f docker-compose.modern.yml down"
    echo ""
else
    echo ""
    echo "❌ Failed to start container. Check logs with:"
    echo "  docker-compose -f docker-compose.modern.yml logs"
fi