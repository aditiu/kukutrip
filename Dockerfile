# Dockerfile for deploying the KukuTrip Travel Itinerary Agent to
# Hugging Face Spaces (or any Docker-based host). Using our own Docker
# image avoids Streamlit Community Cloud's base-image apt issue where
# the Debian "bullseye-security" repo's release metadata has expired
# and breaks `apt-get install` for packages.txt on every build.
#
# Uses a current, actively-maintained Debian release (bookworm slim)
# so system package installs (needed for WeasyPrint's PDF rendering:
# pango/cairo/gdk-pixbuf) don't depend on an EOL repo.

FROM python:3.11-slim-bookworm

# System libraries required by WeasyPrint for PDF generation, plus
# DejaVu fonts used by the PDF templates (see app.py's DejaVu font
# loading in generate_pdf()/generate_pdf_editorial()).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hugging Face Spaces expects the app to listen on port 7860 by default.
EXPOSE 7860

# Match Streamlit's writable-dir expectations used in app.py (CHROMA_DIR,
# HISTORY_DIR, HEADER_CACHE, TEMPLATE_DIR all live under /tmp already, so
# no extra volume/permissions setup is required here).
ENTRYPOINT ["streamlit", "run", "app.py", \
    "--server.port=7860", \
    "--server.address=0.0.0.0", \
    "--server.headless=true", \
    "--browser.gatherUsageStats=false"]
