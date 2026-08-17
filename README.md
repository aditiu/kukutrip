# ✈ Travel Itinerary Agent

A local RAG (Retrieval-Augmented Generation) agent that reads your personal travel documents (Word & PDF) and creates custom itineraries with a beautifully designed PDF — powered by Google Gemini.

---

## Folder Structure

```
travel-agent/
├── docs/            ← Put your .pdf and .docx travel files here
├── app.py           ← Streamlit Web UI (main app)
├── agent.py         ← CLI agent (optional)
├── requirements.txt ← Python dependencies
├── start.sh         ← Quick start script (Linux/macOS)
└── README.md
```

---

## Sharing With a Friend (macOS)

### Option A — ZIP and share

1. Zip the `travel-agent/` folder (excluding `.chroma_db/` and `.chat_history/`):
   ```bash
   cd ~
   zip -r travel-agent.zip travel-agent \
     --exclude "travel-agent/.chroma_db/*" \
     --exclude "travel-agent/.chat_history/*" \
     --exclude "travel-agent/__pycache__/*" \
     --exclude "travel-agent/.venv/*"
   ```
2. Send `travel-agent.zip` to your friend (email, AirDrop, Google Drive, etc.)

### Option B — GitHub (recommended for easy updates)

1. Create a free GitHub repo at https://github.com/new
2. Push the project:
   ```bash
   cd ~/travel-agent
   git init
   git add app.py agent.py requirements.txt README.md start.sh
   git commit -m "Initial commit"
   git remote add origin https://github.com/YOUR_USER/travel-agent.git
   git push -u origin main
   ```
3. Your friend clones it:
   ```bash
   git clone https://github.com/YOUR_USER/travel-agent.git
   cd travel-agent
   ```

---

## macOS Setup (for your friend)

### Prerequisites

Your friend needs:
- **Python 3.10 or higher** — check with `python3 --version`
- **Homebrew** — install from https://brew.sh

### Step 1 — Install system dependencies

WeasyPrint needs a few system libraries. On macOS, install them via Homebrew:

```bash
brew install pango gdk-pixbuf libffi cairo gobject-introspection
```

Also install fonts:
```bash
brew install fontconfig
```

### Step 2 — Create a virtual environment

```bash
cd travel-agent
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3 — Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> On macOS, if `weasyprint` fails to install, try:
> ```bash
> pip install weasyprint --no-binary weasyprint
> ```

### Step 4 — Get a free Google Gemini API key

1. Go to https://aistudio.google.com
2. Sign in with a Google account
3. Click **Get API Key** → **Create API key**
4. Copy the key — it looks like `AIza...`

No credit card required. The `gemini-3.1-flash-lite` model is free.

### Step 5 — Run the app

```bash
cd travel-agent
source .venv/bin/activate      # if not already active
streamlit run app.py
```

The app opens at **http://localhost:8501** in the browser.

### Step 6 — First use

1. Paste the Gemini API key in the sidebar
2. Upload your `.pdf` or `.docx` travel documents in the sidebar
3. Type a request in the chat:
   > "Plan a 7-day Italy trip for 2 adults, 10–17 Sep 2026"
4. The agent will ask clarifying questions if needed
5. When the itinerary is ready, click **Generate PDF**
6. Download your PDF

---

## Troubleshooting on macOS

| Problem | Fix |
|---|---|
| `weasyprint` install error | `brew install pango cairo` then reinstall |
| `ModuleNotFoundError: docx` | `pip install python-docx` |
| `ModuleNotFoundError: pdfplumber` | `pip install pdfplumber` |
| Streamlit not found | Make sure venv is activated: `source .venv/bin/activate` |
| App opens but no index built | Upload documents in the sidebar first |
| Proxy error (corporate network) | Remove proxy lines from `app.py` (`os.environ.setdefault("HTTPS_PROXY"...)`) |

---

## Quick Start Script

If on macOS/Linux, you can use:

```bash
chmod +x start.sh
./start.sh
```

---

## What Your Friend Needs to Provide

- Their own **Google Gemini API key** (free at https://aistudio.google.com)
- Their own travel planning **documents** (.pdf or .docx) placed in the `docs/` folder
- The documents should contain: itinerary details, hotel names, prices, inclusions, etc.
