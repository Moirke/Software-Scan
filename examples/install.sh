#!/bin/bash
# Installation script for Repository Scanner

# Ensure we're in the project root
cd "$(dirname "$0")/.."

echo "=========================================="
echo "Repository Scanner - Installation"
echo "=========================================="
echo ""

# Check Python version
python3 --version > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "Error: Python 3 is not installed"
    exit 1
fi

echo "✓ Python 3 detected"

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install -r requirements.txt --break-system-packages

if [ $? -eq 0 ]; then
    echo "✓ Dependencies installed"
else
    echo "Warning: Some dependencies may not have installed correctly"
fi

# Check for rpm2cpio (optional)
which rpm2cpio > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "✓ rpm2cpio found (RPM support enabled)"
else
    echo "⚠ rpm2cpio not found (RPM support disabled)"
    echo "  To enable RPM support, install: sudo apt-get install rpm2cpio cpio"
fi

# Make scripts executable
chmod +x run-cli.py run-web.py src/cli.py
echo "✓ Scripts made executable"

echo ""
echo "=========================================="
echo "Installation complete!"
echo "=========================================="
echo ""
echo "Quick Start:"
echo "  1. Edit config/config.yaml to configure scanning"
echo "  2. Edit config/prohibited_words.txt to add prohibited words"
echo "  3. Run: ./run-cli.py -c config/config.yaml -r /path/to/repo"
echo ""
echo "Or start the web interface:"
echo "  python3 run-web.py"
echo "  Then open: http://localhost:5000"
echo ""
