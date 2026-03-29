#!/bin/bash

# Simple test script for Kata Judge System

echo "Running basic tests..."

# Test if app can start
echo "Testing app startup..."
python app.py &
APP_PID=$!
sleep 5

# Test if server is responding
if curl -s http://localhost:5000 > /dev/null; then
    echo "✓ Server is responding"
else
    echo "✗ Server is not responding"
    kill $APP_PID
    exit 1
fi

# Test if tests pass
echo "Running unit tests..."
if python test_app.py > /dev/null 2>&1; then
    echo "✓ Unit tests pass"
else
    echo "✗ Unit tests fail"
    kill $APP_PID
    exit 1
fi

# Kill the app
kill $APP_PID

echo "All tests passed!"
