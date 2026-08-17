#!/usr/bin/env bash
# Travel Itinerary Agent — Quick Start
# Works on macOS and Linux

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "✅ Virtual environment activated"
else
    echo "⚠️  No .venv found. Creating one..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    echo "✅ Dependencies installed"
fi

# Create docs folder if missing
mkdir -p docs

echo ""
echo "✈  Starting Travel Itinerary Agent..."
echo "   Open http://localhost:8501 in your browser"
echo ""

streamlit run app.py --server.headless true
