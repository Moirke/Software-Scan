#!/bin/bash
# Demo script to showcase Repository Scanner functionality

echo "=========================================="
echo "Repository Scanner - Demo"
echo "=========================================="
echo ""

# Ensure we're in the right directory
cd "$(dirname "$0")/.."

echo "This demo will scan the demos/test_repo directory"
echo "which contains example files with prohibited words."
echo ""
read -p "Press Enter to continue..."
echo ""

echo "Running scan with verbose output..."
echo ""
echo "Command: ./run-cli.py -c config/config.yaml -r demos/test_repo -v"
echo ""

./run-cli.py -c config/config.yaml -r demos/test_repo -v

echo ""
echo "=========================================="
echo "Demo Complete!"
echo "=========================================="
echo ""
echo "The scanner found violations in:"
echo "  - demos/test_repo/config.json (password, secret)"
echo "  - demos/test_repo/src/app.py (TODO, password, FIXME, api_key)"
echo "  - demos/test_repo/archives/test_archive.zip (TODO inside ZIP)"
echo ""
echo "Notice that the ZIP archive was automatically extracted and scanned!"
echo ""
echo "Try it yourself:"
echo "  ./run-cli.py -c config/config.yaml -r /your/path/here"
echo ""
echo "Or start the web interface:"
echo "  python3 run-web.py"
echo ""
