"""
Travel Itinerary Agent — Streamlit Web UI
Run:  streamlit run app.py

"""

import io
import json
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

# ── Proxy (corporate network only — comment out or unset if not needed) ───────
# If HTTPS_PROXY env var is already set in the shell, that value is used.
# On a home/mac machine with no proxy, leave these unset or set TRAVEL_PROXY="" to skip.
_proxy = os.environ.get("TRAVEL_PROXY", "")   # set to "" to disable
if _proxy:
    os.environ.setdefault("HTTPS_PROXY", _proxy)
    os.environ.setdefault("HTTP_PROXY",  _proxy)

import streamlit as st

# ── Paths — writable dirs use /tmp/ for Streamlit Community Cloud ──────────────
_APP_DIR     = Path(__file__).parent
DOCS_DIR     = _APP_DIR / "docs"
CHROMA_DIR   = Path("/tmp/.chroma_db")
HISTORY_DIR  = Path("/tmp/.chat_history")
TEMPLATE_DIR = Path("/tmp/template")
RETENTION    = 5   # days

# ── Company branding (constant across all itineraries) ────────────────────────
COMPANY_NAME  = "Ankur Sharma"
COMPANY_PHONE = "+918929838899"
COMPANY_WEB   = "www.kukutrip.com"
COMPANY_EMAIL = "info@kukutrip.com"

st.set_page_config(page_title="✈ Travel Itinerary Agent", page_icon="✈", layout="wide")

# ── Session / history helpers ─────────────────────────────────────────────────

def _session_ts(stem: str) -> datetime:
    return datetime.strptime(stem, "%Y-%m-%d_%H-%M-%S")

def purge_old_sessions():
    if not HISTORY_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=RETENTION)
    for f in HISTORY_DIR.glob("*.json"):
        try:
            if _session_ts(f.stem) < cutoff:
                f.unlink()
        except Exception:
            pass

def list_sessions():
    if not HISTORY_DIR.exists():
        return []
    return sorted(HISTORY_DIR.glob("*.json"), reverse=True)

def save_session(sid: str, msgs: list):
    HISTORY_DIR.mkdir(exist_ok=True)
    (HISTORY_DIR / f"{sid}.json").write_text(
        json.dumps({"session_id": sid, "messages": msgs}, indent=2)
    )

def load_session(path: Path) -> list:
    return json.loads(path.read_text())["messages"]

def new_sid() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

purge_old_sessions()

if "session_id" not in st.session_state:
    st.session_state.session_id = new_sid()

# ── PDF generation — Italy template pixel-perfect replication ─────────────────

HEADER_CACHE = Path("/tmp/.header_cache")
HEADER_CACHE.mkdir(exist_ok=True)

def _wiki_image(search: str, proxies: dict, headers: dict) -> str | None:
    """Fetch a Wikipedia article's originalimage URL, skipping flags/SVGs/maps."""
    import requests
    try:
        api = f"https://en.wikipedia.org/api/rest_v1/page/summary/{search.replace(' ', '_')}"
        r = requests.get(api, proxies=proxies, timeout=12, headers=headers)
        if r.status_code != 200:
            return None
        data = r.json()
        img_url = (data.get("originalimage") or data.get("thumbnail") or {}).get("source", "")
        if not img_url:
            return None
        skip_words = ("Flag", "Map", "Coat_of", "Emblem", "Seal_of", "Logo")
        if img_url.lower().endswith(".svg") or any(w in img_url for w in skip_words):
            return None
        return img_url
    except Exception:
        return None


def _wikimedia_search_image(query: str, proxies: dict, headers: dict) -> str | None:
    """Search Wikimedia Commons for a landscape image matching the query."""
    import requests
    try:
        params = {
            "action": "query",
            "list": "search",
            "srsearch": f"{query} landscape",
            "srnamespace": "6",   # File: namespace
            "srlimit": "5",
            "format": "json",
        }
        r = requests.get("https://commons.wikimedia.org/w/api.php",
                         params=params, proxies=proxies, timeout=12, headers=headers)
        if r.status_code != 200:
            return None
        results = r.json().get("query", {}).get("search", [])
        for res in results:
            title = res.get("title", "")
            if not title.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            # Get direct URL for this file
            info_r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={"action": "query", "titles": title,
                        "prop": "imageinfo", "iiprop": "url", "format": "json"},
                proxies=proxies, timeout=10, headers=headers)
            if info_r.status_code == 200:
                pages = info_r.json().get("query", {}).get("pages", {})
                for page in pages.values():
                    url = (page.get("imageinfo") or [{}])[0].get("url", "")
                    skip_words = ("Flag", "Map", "Coat_of", "Emblem", "Seal_of", "Logo")
                    if url and not url.lower().endswith(".svg") and not any(w in url for w in skip_words):
                        return url
    except Exception:
        pass
    return None


def fetch_destination_image(keyword: str, _dbg: list | None = None) -> Path | None:
    """
    Dynamically fetch a scenic hero image for the destination.

    Strategy (in priority order):
    1. Specific landmark/location Wikipedia article (e.g. "Dolomites")
    2. Tourism_in_{Country} article
    3. Geography_of_{Country} article
    4. Country Wikipedia article
    5. Wikimedia Commons search for the keyword
    6. picsum.photos seeded fallback
    """
    import hashlib, requests
    slug = hashlib.md5(keyword.lower().encode()).hexdigest()[:12]
    cached = HEADER_CACHE / f"{slug}.jpg"
    if cached.exists():
        return cached

    _p = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    proxies = {"https": _p, "http": _p} if _p else {}
    headers_h = {"User-Agent": "TravelItineraryAgent/1.0 (travel-pdf-generator; free use)"}

    def _log(msg):
        if _dbg is not None:
            _dbg.append(msg)

    # Build search candidates — keyword may be "Dolomites Italy" or "Kyoto" or "Tbilisi"
    words = keyword.strip().split()
    primary = words[0].title()
    country = words[-1].title() if len(words) > 1 else primary

    candidates = [
        keyword.replace(" ", "_").title(),
        primary,
        f"Tourism_in_{country}",
        f"Tourism_in_{primary}",
        f"Geography_of_{country}",
        f"{country}",
    ]

    img_url = None
    for search in candidates:
        img_url = _wiki_image(search, proxies, headers_h)
        if img_url:
            _log(f"✅ Wikipedia image found via '{search}'")
            break
    if not img_url:
        _log(f"⚠️ Wikipedia: no image found for any candidate")

    # Wikimedia Commons full-text search
    if not img_url:
        img_url = _wikimedia_search_image(keyword, proxies, headers_h)
        if img_url:
            _log(f"✅ Wikimedia Commons image found")
        else:
            _log(f"⚠️ Wikimedia Commons: no image found")

    if not img_url:
        # Last fallback: picsum.photos — reliable, free, no API key, seeded by keyword
        try:
            seed = int(hashlib.md5(keyword.lower().encode()).hexdigest()[:8], 16) % 1000000
            picsum_url = f"https://picsum.photos/seed/{seed}/1600/900"
            img_r = requests.get(picsum_url, proxies=proxies, timeout=20,
                                 headers=headers_h, allow_redirects=True)
            if img_r.status_code == 200 and len(img_r.content) > 15000:
                from PIL import Image as PILImage
                from io import BytesIO as BytesIO2
                pil = PILImage.open(BytesIO2(img_r.content)).convert("RGB")
                pil.save(str(cached), "JPEG", quality=92, optimize=True)
                _log(f"✅ picsum.photos fallback used")
                return cached
            else:
                _log(f"⚠️ picsum.photos: status={img_r.status_code}, size={len(img_r.content)}")
        except Exception as e:
            _log(f"❌ picsum.photos error: {e}")
        return None

    # Download and convert to landscape JPEG — retry up to 3 times
    for attempt in range(1, 4):
        try:
            img_r = requests.get(img_url, proxies=proxies, timeout=45, headers=headers_h)
            if img_r.status_code == 200 and len(img_r.content) > 15000:
                from PIL import Image as PILImage
                from io import BytesIO as BytesIO2
                pil = PILImage.open(BytesIO2(img_r.content)).convert("RGB")
                # Crop to 16:9 if portrait
                w, h = pil.size
                if h > w:
                    new_h = int(w * 9 / 16)
                    top = max(0, (h - new_h) // 4)
                    pil = pil.crop((0, top, w, top + new_h))
                # Resize to max 1200×675 — keeps file small for base64 embed in PDF
                pil.thumbnail((1200, 675), PILImage.LANCZOS)
                pil.save(str(cached), "JPEG", quality=85, optimize=True)
                _log(f"✅ Image downloaded and saved ({pil.width}x{pil.height}px, attempt {attempt})")
                return cached
            else:
                _log(f"⚠️ Attempt {attempt}: status={img_r.status_code}, size={len(img_r.content)}")
                if img_r.status_code in (429, 503) and attempt < 3:
                    import time; time.sleep(2 * attempt)
                else:
                    break
        except Exception as e:
            _log(f"❌ Attempt {attempt} error: {e}")
            if attempt < 3:
                import time; time.sleep(2)

    # Final fallback after Wikipedia download failure: picsum
    try:
        seed = int(hashlib.md5(keyword.lower().encode()).hexdigest()[:8], 16) % 1000000
        picsum_url = f"https://picsum.photos/seed/{seed}/1600/900"
        img_r = requests.get(picsum_url, proxies=proxies, timeout=20,
                             headers=headers_h, allow_redirects=True)
        if img_r.status_code == 200 and len(img_r.content) > 15000:
            from PIL import Image as PILImage
            from io import BytesIO as BytesIO2
            pil = PILImage.open(BytesIO2(img_r.content)).convert("RGB")
            pil.thumbnail((1200, 675), PILImage.LANCZOS)
            pil.save(str(cached), "JPEG", quality=85, optimize=True)
            _log(f"✅ picsum.photos used after Wikipedia download failure")
            return cached
        else:
            _log(f"⚠️ picsum fallback also failed: {img_r.status_code}")
    except Exception as e:
        _log(f"❌ picsum fallback error: {e}")
    return None

def _h(text: str) -> str:
    """HTML-escape a string."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def generate_pdf(title: str, content: str, meta: dict,
                 header_img_path: Path | None = None) -> bytes:
    """
    Render a fixed HTML/CSS travel itinerary template via WeasyPrint.
    Design follows the Italy reference PDF exactly:
    rounded cards, gradient hero, pill badges, green/pink/yellow sections.
    Uses local DejaVu Sans fonts — no internet required.
    """
    import base64
    from weasyprint import HTML as WH

    # ── Embed hero image as base64 data-URI ───────────────────────────────────
    if header_img_path and header_img_path.exists():
        img_b64 = base64.b64encode(header_img_path.read_bytes()).decode()
        # Dark gradient overlay ensures text is always readable over the photo
        hero_bg = (
            "linear-gradient(180deg,rgba(30,10,40,0.68) 0%,"
            "rgba(100,20,40,0.52) 50%,rgba(30,10,40,0.72) 100%),"
            f"url('data:image/jpeg;base64,{img_b64}') center/cover no-repeat"
        )
    else:
        hero_bg = (
            "linear-gradient(180deg,#4B275B 0%,#A63C55 45%,#E78A52 100%)"
        )

    pkg_name  = _h(meta.get("package_name", meta.get("destination", title)).upper())
    route_txt = _h(meta.get("route", ""))
    dates_txt = _h(meta.get("dates", ""))
    nights_txt = _h(meta.get("nights", ""))

    # ── Hero dates line: prefer dates, else show nights ────────────────────────
    hero_dates = dates_txt or nights_txt

    # ── Metadata pills — centered group ───────────────────────────────────────
    pill_items = [
        meta.get("dates", ""), meta.get("nights", ""),
        meta.get("persons", ""), meta.get("transport", ""),
    ]
    pills_html = "".join(
        f'<span class="pill">{_h(p)}</span>'
        for p in pill_items if p
    )

    # ── Price fallback: generate reasonable placeholder if missing ─────────────
    amount = meta.get("amount", "")
    if not amount or "not available" in str(amount).lower():
        # Generate a placeholder based on nights × travellers × daily rate
        nights_num = 0
        try:
            nights_num = int(re.search(r'(\d+)\s*[Nn]ight', meta.get("nights", "0")).group(1))
        except Exception:
            pass
        persons_num = 1
        try:
            persons_num = max(1, int(re.search(r'(\d+)\s*[Aa]dult', meta.get("persons", "1")).group(1)))
        except Exception:
            pass
        # ~INR 5,000 per night per person as placeholder
        placeholder_amt = max(15000, nights_num * persons_num * 5000)
        # Round to nearest 5000
        placeholder_amt = (placeholder_amt // 5000) * 5000
        amount = f"INR {placeholder_amt:,}/- (approx.)"

    # ── Package price HTML (uses computed amount with fallback) ────────────────
    persons_rooms = "   ·   ".join(filter(None, [
        meta.get("persons", ""), meta.get("transport", "")]))
    price_html = f"""
        <div class="price-card">
          <div class="price-label">TOTAL PACKAGE COST</div>
          <div class="price">{_h(str(amount))}</div>
          <div class="price-meta">{_h(persons_rooms)}</div>
        </div>"""

    # ── Day cards ─────────────────────────────────────────────────────────────
    days_html = ""
    all_days = meta.get("days", [])
    last_day_num = all_days[-1].get("day") if all_days else None
    for day in all_days:
        acts = "".join(f"<li>{_h(a)}</li>" for a in day.get("activities", []))
        # Suppress "Overnight in..." on the final/departure day
        is_last = day.get("day") == last_day_num
        overnight_city = day.get("overnight", "")
        overnight = (f'<div class="overnight">Overnight in {_h(overnight_city)}</div>'
                     if overnight_city and not is_last else "")
        days_html += f"""
        <div class="day-card">
          <div class="day-num">
            <div class="day-num-val">{_h(str(day.get('day','')))}</div>
            <div class="day-num-date">{_h(str(day.get('date','')).upper())}</div>
          </div>
          <div class="day-body">
            <div class="day-title">{_h(day.get('title',''))}</div>
            <ul>{acts}</ul>
            {overnight}
          </div>
        </div>"""

    # ── Hotels table ──────────────────────────────────────────────────────────
    hotels_html = ""
    if meta.get("hotels"):
        rows = "".join(
            f'<tr><td>{_h(h.get("city",""))}</td>'
            f'<td>{_h(h.get("hotel",""))}</td>'
            f'<td>{_h(h.get("dates",""))}</td></tr>'
            for h in meta["hotels"]
        )
        hotels_html = f"""
        <div class="section">
          <div class="section-title">Hotels Confirmed</div>
          <table class="hotel-table">
            <thead><tr>
              <th>City / Nights</th><th>Hotel</th><th>Dates</th>
            </tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""

    # ── Highlights pills ──────────────────────────────────────────────────────
    highlights_html = ""
    if meta.get("highlights"):
        pills = "".join(f'<span class="hi-pill">{_h(h)}</span>'
                        for h in meta["highlights"])
        highlights_html = f"""
        <div class="section">
          <div class="section-title">Package Highlights</div>
          <div class="hi-pills">{pills}</div>
        </div>"""

    # ── Inclusions / Exclusions ───────────────────────────────────────────────
    inc = meta.get("inclusions", [])
    exc = meta.get("exclusions", [])
    ie_html = ""
    if inc or exc:
        inc_items = "".join(f"<li>{_h(i)}</li>" for i in inc)
        exc_items = "".join(f"<li>{_h(e)}</li>" for e in exc)
        ie_html = f"""
        <div class="ie-row">
          <div class="inc-card">
            <div class="inc-title"><span class="chk">&#10003;</span> Inclusions</div>
            <ul>{inc_items}</ul>
          </div>
          <div class="exc-card">
            <div class="exc-title"><span class="xmark">&#10005;</span> Exclusions</div>
            <ul>{exc_items}</ul>
          </div>
        </div>"""

    # ── Notes ─────────────────────────────────────────────────────────────────
    notes = meta.get("notes", [])
    notes_html = ""
    if notes:
        items = "".join(f"<li>{_h(n)}</li>" for n in notes)
        notes_html = f"""
        <div class="notes-card">
          <div class="notes-title">📋 Important Notes</div>
          <ul>{items}</ul>
        </div>"""

    # ── DejaVu font paths ──────────────────────────────────────────────────────
    dv_dir = "/usr/share/fonts/truetype/dejavu"
    dv_regular = Path(f"{dv_dir}/DejaVuSans.ttf")
    dv_bold    = Path(f"{dv_dir}/DejaVuSans-Bold.ttf")

    font_face = ""
    if dv_regular.exists() and dv_bold.exists():
        r_b64 = base64.b64encode(dv_regular.read_bytes()).decode()
        b_b64 = base64.b64encode(dv_bold.read_bytes()).decode()
        font_face = f"""
        @font-face {{
            font-family: "DejaVuSans";
            font-weight: normal;
            src: url("data:font/truetype;base64,{r_b64}");
        }}
        @font-face {{
            font-family: "DejaVuSans";
            font-weight: bold;
            src: url("data:font/truetype;base64,{b_b64}");
        }}"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
{font_face}

@page {{
    size: A4;
    margin: 14mm 16mm;
}}

* {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}}

body {{
    font-family: "DejaVuSans", Arial, sans-serif;
    font-size: 11px;
    color: #333333;
    background: #FFFFFF;
}}

.page {{
    width: 100%;
    background: #FFFFFF;
}}

/* ── HERO ─────────────────────────────────────────────── */
.hero {{
    border-radius: 7px;
    overflow: hidden;
    height: 60mm !important;
    min-height: 60mm !important;
    max-height: 60mm !important;
    flex-shrink: 0;
    background: {hero_bg};
    background-size: cover !important;
    background-position: center center !important;
    background-repeat: no-repeat !important;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    margin-bottom: 10px;
    page-break-after: avoid;
    break-after: avoid;
}}
.hero-content {{
    width: 100%;
    padding: 20px 24px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
}}
.hero-title {{
    font-size: 25px;
    font-weight: bold;
    letter-spacing: 0.5px;
    color: #FFFFFF;
    text-transform: uppercase;
    line-height: 1.2;
    text-align: center;
    margin-left: auto;
    margin-right: auto;
}}
.hero-route {{
    font-size: 13px;
    color: #FFFFFF;
    margin-top: 6px;
    opacity: 0.9;
    text-align: center;
    margin-left: auto;
    margin-right: auto;
}}
.hero-dates {{
    font-size: 11px;
    color: #FFFFFF;
    margin-top: 5px;
    opacity: 0.85;
    text-align: center;
    margin-left: auto;
    margin-right: auto;
}}

/* ── PILLS ────────────────────────────────────────────── */
.pills {{
    width: 100%;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    align-items: center;
    gap: 5px;
    margin-bottom: 12px;
}}
.pill {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 6px 13px;
    border-radius: 999px;
    background: #FBECEC;
    border: 1px solid #E8DAD6;
    color: #8F1C1C;
    font-size: 10px;
    font-weight: bold;
    text-align: center;
}}

/* ── SECTION HEADINGS ─────────────────────────────────── */
.section {{
    margin-bottom: 12px;
}}
.section-title {{
    font-size: 17px;
    font-weight: bold;
    color: #9E1B1B;
    margin: 10px 0 7px 0;
    text-align: left;
}}

/* ── DAY CARDS ────────────────────────────────────────── */
.day-card {{
    display: flex;
    width: 100%;
    background: #FCF8F6;
    border: 1px solid #E8DAD6;
    border-radius: 7px;
    overflow: hidden;
    margin-bottom: 7px;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.day-num {{
    width: 52px;
    min-width: 52px;
    background: #9E1B1B;
    color: #FFFFFF;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 10px 4px;
    text-align: center;
}}
.day-num-val {{
    font-size: 20px;
    font-weight: bold;
    line-height: 1;
}}
.day-num-date {{
    font-size: 8px;
    font-weight: bold;
    margin-top: 3px;
    opacity: 0.85;
}}
.day-body {{
    padding: 10px 13px;
    flex: 1;
}}
.day-title {{
    font-size: 13px;
    font-weight: bold;
    color: #8F1C1C;
    margin-bottom: 5px;
}}
.day-body ul {{
    padding-left: 14px;
    margin: 0;
}}
.day-body li {{
    font-size: 10.5px;
    line-height: 1.5;
    margin-bottom: 2px;
    color: #333333;
}}
.overnight {{
    font-size: 10px;
    font-weight: bold;
    color: #8F1C1C;
    margin-top: 6px;
}}

/* ── HOTEL TABLE ──────────────────────────────────────── */
.hotel-table {{
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    overflow: hidden;
    border-radius: 7px;
    border: 1px solid #E8DAD6;
}}
.hotel-table thead tr {{
    background: #9E1B1B;
}}
.hotel-table th {{
    padding: 8px 9px;
    font-size: 10px;
    font-weight: bold;
    color: #FFFFFF;
    text-align: left;
}}
.hotel-table td {{
    padding: 7px 9px;
    font-size: 10px;
    color: #444444;
    border-top: 1px solid #EDD8D8;
}}
.hotel-table tr:nth-child(even) td {{
    background: #FBF0F0;
}}

/* ── PRICE CARD ───────────────────────────────────────── */
.price-card {{
    background: linear-gradient(135deg,#9E1B1B,#C62828);
    color: #FFFFFF;
    border-radius: 7px;
    padding: 13px 20px;
    text-align: center;
    margin-top: 10px;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.price-label {{
    font-size: 9px;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    opacity: 0.85;
}}
.price {{
    font-size: 24px;
    font-weight: bold;
    margin: 5px 0 3px;
}}
.price-meta {{
    font-size: 9px;
    opacity: 0.8;
}}

/* ── HIGHLIGHTS ───────────────────────────────────────── */
.hi-pills {{
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
}}
.hi-pill {{
    display: inline-block;
    padding: 6px 11px;
    border-radius: 999px;
    background: #FFF3DD;
    border: 1px solid #EAD8B4;
    color: #8A5A20;
    font-size: 9.5px;
    font-weight: bold;
}}

/* ── INCLUSIONS / EXCLUSIONS ──────────────────────────── */
.ie-row {{
    display: flex;
    gap: 8px;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.inc-card {{
    flex: 1;
    background: #EFFAF1;
    border: 1px solid #CDE8D1;
    border-radius: 7px;
    padding: 10px 12px;
}}
.exc-card {{
    flex: 1;
    background: #FFF1F1;
    border: 1px solid #F0D1D1;
    border-radius: 7px;
    padding: 10px 12px;
}}
.inc-title {{
    color: #3B7D44;
    font-size: 12px;
    font-weight: bold;
    margin-bottom: 6px;
}}
.exc-title {{
    color: #A52A2A;
    font-size: 12px;
    font-weight: bold;
    margin-bottom: 6px;
}}
.chk {{
    display: inline-block;
    color: #3B7D44;
    font-weight: bold;
    font-size: 13px;
    line-height: 1;
    margin-right: 3px;
}}
.xmark {{
    display: inline-block;
    color: #A52A2A;
    font-weight: bold;
    font-size: 13px;
    line-height: 1;
    margin-right: 3px;
}}
.inc-card ul, .exc-card ul {{
    padding-left: 13px;
    margin: 0;
}}
.inc-card li, .exc-card li {{
    font-size: 10px;
    line-height: 1.55;
    margin-bottom: 2px;
}}

/* ── NOTES ────────────────────────────────────────────── */
.notes-card {{
    background: #FFF8E7;
    border: 1px solid #EEDFB5;
    border-radius: 7px;
    padding: 10px 13px;
    margin-top: 9px;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.notes-title {{
    color: #8A651C;
    font-size: 12px;
    font-weight: bold;
    margin-bottom: 6px;
}}
.notes-card ul {{
    padding-left: 13px;
    margin: 0;
}}
.notes-card li {{
    font-size: 10px;
    line-height: 1.55;
    margin-bottom: 2px;
    color: #555533;
}}

/* ── CONTACT FOOTER ───────────────────────────────────── */
.contact-footer {{
    background: linear-gradient(135deg,#9E1B1B,#C62828);
    color: #FFFFFF;
    border-radius: 7px;
    padding: 11px 16px;
    text-align: center;
    margin-top: 12px;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.agent-name {{
    font-size: 14px;
    font-weight: bold;
    margin-bottom: 3px;
}}
.contact-details {{
    font-size: 9px;
    font-weight: bold;
    opacity: 0.88;
}}
</style>
</head>
<body>
<div class="page">

  <!-- HERO -->
  <div class="hero">
    <div class="hero-content">
      <div class="hero-title">{pkg_name}</div>
      {f'<div class="hero-route">{route_txt}</div>' if route_txt else ''}
      {f'<div class="hero-dates">{hero_dates}</div>' if hero_dates else ''}
    </div>
  </div>

  <!-- PILLS -->
  <div class="pills">{pills_html}</div>

  <!-- DAY-WISE ITINERARY -->
  <div class="section">
    <div class="section-title">Day-wise Itinerary</div>
    {days_html}
  </div>

  {hotels_html}
  {price_html}
  {highlights_html}

  <!-- INCLUSIONS / EXCLUSIONS -->
  {ie_html}

  {notes_html}

  <!-- CONTACT FOOTER -->
  <div class="contact-footer">
    <div class="agent-name">{_h(COMPANY_NAME)}</div>
    <div class="contact-details">{_h(COMPANY_PHONE)} &nbsp;·&nbsp; {_h(COMPANY_WEB)} &nbsp;·&nbsp; {_h(COMPANY_EMAIL)}</div>
  </div>

</div>
</body>
</html>"""

    buf = io.BytesIO()
    WH(string=html).write_pdf(buf)
    return buf.getvalue()


# ── LLM-generated HTML → PDF ─────────────────────────────────────────────────

def generate_html_pdf(meta: dict, api_key: str, model: str,
                      header_img_path: Path | None = None) -> bytes:
    """Ask Gemini to design a modern HTML itinerary, then render to PDF via WeasyPrint."""
    import base64, requests
    from weasyprint import HTML as WH, CSS

    # Embed header image as base64 if available
    img_tag = '<div class="hero-fallback"></div>'
    if header_img_path and header_img_path.exists():
        img_b64 = base64.b64encode(header_img_path.read_bytes()).decode()
        img_tag = f'<img class="hero-img" src="data:image/jpeg;base64,{img_b64}" />'

    # Serialize meta for Gemini
    meta_json = json.dumps(meta, indent=2)

    system = (
        "You are a world-class HTML/CSS designer specialising in travel brochures. "
        "Generate a complete, self-contained HTML page for a travel itinerary PDF. "
        "Requirements:\n"
        "- Modern, professional design inspired by luxury travel brochures\n"
        "- Use CSS variables, gradients, and subtle shadows\n"
        "- Color palette: deep purple/navy (#2A1830) for accents, maroon (#8A1A1A) for headings, "
        "warm white (#FAFAF8) background, amber (#8A6300) for highlights\n"
        "- Page size A4, all content self-contained (no external URLs except fonts)\n"
        "- First section: full-width hero header with the destination image tag provided\n"
        "- Overlay destination name, route cities, and trip details on the hero image\n"
        "- Day cards: numbered boxes on the left with dark purple background, activities on the right\n"
        "- Hotels table, Package highlights grid, Inclusions/Exclusions two-column layout\n"
        "- Footer with company name, phone, web, email\n"
        "- Use Google Fonts (Playfair Display for headings, Inter for body) via @import in <style>\n"
        "- Include the hero image placeholder where indicated: {IMG_TAG}\n"
        "- Print-friendly: @media print rules, page breaks where appropriate\n"
        "- Output ONLY the complete HTML. No explanation. No markdown fences.\n\n"
        f"Company: {COMPANY_NAME} | {COMPANY_PHONE} | {COMPANY_WEB} | {COMPANY_EMAIL}\n\n"
        f"Itinerary data:\n{meta_json}\n\n"
        f"Replace {{IMG_TAG}} with: {img_tag}"
    )

    _p2 = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    proxies = {"https": _p2, "http": _p2} if _p2 else {}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": "Generate the travel itinerary HTML now."}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192}
    }
    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"},
                         params={"key": api_key}, proxies=proxies, timeout=120)
    resp.raise_for_status()
    html_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    # Strip markdown fences if Gemini wrapped it
    html_text = re.sub(r'^```[a-z]*\n?', '', html_text.strip())
    html_text = re.sub(r'\n?```$', '', html_text.strip())

    # Render HTML → PDF
    buf = io.BytesIO()
    WH(string=html_text).write_pdf(buf)
    return buf.getvalue()


# ── Document loaders ─────────────────────────────────────────────────────────

def load_pdf_text(path: Path) -> str:
    import pdfplumber
    parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
    return "\n".join(parts)

def load_docx_text(path: Path) -> str:
    from docx import Document
    doc = Document(path)
    parts = []
    # Paragraphs
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text.strip())
    # Tables — critical for hotel/price data stored in table cells
    for table in doc.tables:
        headers = [c.text.strip() for c in table.rows[0].cells] if table.rows else []
        for row in table.rows[1:]:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                if headers:
                    row_text = " | ".join(
                        f"{h}: {v}" for h, v in zip(headers, cells) if v
                    )
                else:
                    row_text = " | ".join(c for c in cells if c)
                parts.append(row_text)
    return "\n".join(parts)


# ── Vector store ──────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner="Building document index…")
def build_vectorstore():
    import chromadb
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    ef     = DefaultEmbeddingFunction()
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    name   = "travel_docs"

    try:
        col = client.get_collection(name, embedding_function=ef)
        if col.count() > 0:
            return col
        client.delete_collection(name)
    except Exception:
        pass

    DOCS_DIR.mkdir(exist_ok=True)
    raw = []
    for f in sorted(DOCS_DIR.iterdir()):
        try:
            if f.suffix.lower() == ".pdf":
                raw.append({"source": f.name, "content": load_pdf_text(f)})
            elif f.suffix.lower() == ".docx":
                raw.append({"source": f.name, "content": load_docx_text(f)})
        except Exception as e:
            st.warning(f"Could not load {f.name}: {e}")

    if not raw:
        return None

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    ids, docs, metas = [], [], []
    for d in raw:
        for i, chunk in enumerate(splitter.split_text(d["content"])):
            ids.append(f"{d['source']}_{i}")
            docs.append(chunk)
            metas.append({"source": d["source"]})

    col = client.create_collection(name, embedding_function=ef)
    for start in range(0, len(docs), 100):
        col.add(ids=ids[start:start+100], documents=docs[start:start+100], metadatas=metas[start:start+100])
    return col


# ── Gemini call ───────────────────────────────────────────────────────────────

def call_gemini(api_key: str, model: str, system: str, user: str) -> str:
    import requests
    url     = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    _p = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    proxies = {"https": _p, "http": _p} if _p else {}
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096}
    }
    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"},
                         params={"key": api_key}, proxies=proxies, timeout=90)
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def get_answer(col, question: str, history: list, api_key: str, model: str) -> tuple[str, list[str], dict]:
    # Primary query
    results = col.query(query_texts=[question], n_results=12)
    docs_seen = set(results["documents"][0])
    all_docs = list(results["documents"][0])
    sources = list({m["source"] for m in results["metadatas"][0]})

    # Always include a secondary hotel-specific retrieval so hotel tables are always in context
    hotel_query = "hotel accommodation destination star category 5 star 4 star 3 star"
    h_results = col.query(query_texts=[hotel_query], n_results=8)
    for d in h_results["documents"][0]:
        if d not in docs_seen:
            all_docs.append(d)
            docs_seen.add(d)
    for m in h_results["metadatas"][0]:
        sources.append(m["source"])
    sources = list(set(sources))

    context = "\n\n".join(all_docs)

    history_text = "".join(
        f"{'User' if m['role']=='user' else 'Assistant'}: {m['content']}\n"
        for m in history[-6:]
    )

    system = (
        "You are a professional Travel Itinerary Generation Agent.\n\n"

        "## KNOWLEDGE BASE — SOURCE OF TRUTH\n"
        "The documents provided are your ONLY source for: hotels, activities, prices, inclusions, "
        "exclusions, transfers, tours, and company policies. NEVER invent travel information.\n\n"

        "## CLARIFICATION WORKFLOW (FOLLOW EXACTLY)\n\n"
        "STEP 1 — Understand the request.\n"
        "STEP 2 — Search the knowledge base.\n"
        "STEP 3 — Check if critical information is missing:\n"
        "  • Country / destination — if missing, ASK\n"
        "  • Duration (days/nights) — if missing and cannot be inferred, ASK\n"
        "  • Traveller count — if missing and affects hotels/pricing, ASK\n"
        "  • Dates — only ask if they are needed for hotel assignment or pricing\n"
        "STEP 4 — Check if multiple materially different options exist in the KB:\n"
        "  • Multiple itinerary routes → SHOW OPTIONS, wait for user choice\n"
        "  • Multiple hotels for same city → SHOW OPTIONS, wait for user choice\n"
        "  • Multiple transportation modes → SHOW OPTIONS, wait for user choice\n"
        "  • Multiple packages with different durations/prices → SHOW OPTIONS\n"
        "STEP 5 — If info missing OR options exist: respond with Markdown ONLY (no ---JSON---).\n"
        "STEP 6 — When ALL information is confirmed: generate full itinerary + ---JSON--- block.\n\n"

        "## SMART DEFAULTS (DO NOT ASK UNNECESSARILY)\n"
        "- If only ONE option exists in KB → use it automatically\n"
        "- If user's request exactly matches one option → use it automatically\n"
        "- If user says 'you choose' or 'best option' → pick and explain, then generate\n"
        "- Do NOT ask for budget unless needed to filter between options\n"
        "- Do NOT ask for rooms if not relevant to the itinerary\n"
        "- Combine all missing questions into ONE message (max 3 questions at once)\n\n"

        "## OPTION PRESENTATION FORMAT\n"
        "When presenting options, use numbered list:\n"
        "**1.** Rome + Florence + Venice — 7 Days\n"
        "**2.** Rome + Florence + Dolomites — 7 Days\n"
        "Then ask: 'Which option would you prefer? Also let me know your travel dates and traveller count.'\n\n"

        "## WHEN READY — RESPONSE FORMAT\n"
        "Only when all choices are confirmed, respond in TWO parts separated by ---READY---\n\n"
        "PART 1: Friendly Markdown itinerary summary ending with:\n"
        "'✅ **Your itinerary is ready. Click Generate PDF to create your document.**'\n\n"
        "---READY---\n\n"
        "PART 2: Structured JSON (schema below). Do NOT output ---READY--- during clarification.\n\n"

        "## JSON SCHEMA\n"
        "{\n"
        '  "destination": "Country name",\n'
        '  "package_name": "Creative title e.g. GEORGIA WITH CAUCASUS MOUNTAINS",\n'
        '  "image_keyword": "Most iconic landmark e.g. Dolomites, Gergeti Church Kazbegi, Colosseum Rome",\n'
        '  "route": "City1 · City2 · City3",\n'
        '  "dates": "DD MMM – DD MMM YYYY (or empty if not provided)",\n'
        '  "nights": "N Nights / N Days",\n'
        '  "persons": "N Adults · N Children · N Rooms",\n'
        '  "transport": "e.g. Private Cab, Self Drive",\n'
        '  "days": [\n'
        '    {"day": 1, "date": "DD MMM or empty", "title": "Day title",\n'
        '     "activities": ["Activity 1", "Activity 2"],\n'
        '     "overnight": "City (omit on final/departure day)"}\n'
        "  ],\n"
        '  "hotels": [{"city": "City (N Nights)", "hotel": "Hotel — Meal Plan", "dates": "DD–DD MMM"}],\n'
        '  "highlights": ["Highlight 1"],\n'
        '  "inclusions": ["Only applicable items"],\n'
        '  "exclusions": ["Only applicable items"],\n'
        '  "notes": ["Only applicable notes"],\n'
        '  "amount": "Price from KB e.g. INR 1,45,000 per person — omit if not in KB"\n'
        "}\n\n"

        "## PAX / PERSON / ADULT NORMALIZATION\n"
        "The terms Pax, PAX, Person, Persons, People, Adult, Adults, 'No. of Pax', 'No. of Persons', "
        "'Number of Adults' all refer to the SAME field: number of people travelling.\n"
        "- '2 Pax' = '2 Persons' = '2 Adults' — use whichever the user provided, map to the same count.\n"
        "- If the user provides conflicting values (e.g. '2 Pax' and '3 Adults' in the same message), "
        "ask for clarification before proceeding.\n"
        "- In the JSON 'persons' field, always output as 'N Adults' regardless of which synonym was used.\n\n"

        "## ACCOMMODATION SELECTION — KNOWLEDGE BASE DRIVEN (MANDATORY)\n"
        "This section governs ALL accommodation selection. Follow exactly.\n\n"

        "### A. NEVER HARDCODE\n"
        "Do NOT hardcode countries, cities, hotel names, star categories, or category labels (A/B/C/D).\n"
        "The KB is the ONLY source of truth for hotel names and categories.\n\n"

        "### B. CATEGORY DISCOVERY\n"
        "If the user has NOT specified a hotel category:\n"
        "  1. Search the KB for accommodation data relevant to this destination/country.\n"
        "  2. Extract the available categories (whatever labels the KB uses — '5 Star', '4 Star – C', 'Luxury', etc.).\n"
        "  3. Present those exact categories to the user and ask them to choose.\n"
        "  DO NOT assume or default to any category.\n\n"

        "### C. CATEGORY NORMALIZATION (formatting only)\n"
        "Normalize only formatting differences that refer to the SAME category:\n"
        "  '4 Star – C' = '4 Star C' = '4-Star C' = '4* C' = '4 Star-C'\n"
        "  '5 Star' = '5*' = '5-Star'\n"
        "  Different sub-categories (A, B, C, D) are DISTINCT — never equate them.\n"
        "  DO NOT assume Luxury=5 Star, Premium=4 Star, Standard=3 Star unless KB says so.\n\n"

        "### D. PER-DESTINATION LOOKUP\n"
        "For EVERY destination in the itinerary, independently:\n"
        "  1. Identify the destination name.\n"
        "  2. Find the user's requested accommodation category.\n"
        "  3. Search KB: Country → Destination → Exact Category → Hotel names.\n"
        "  4. The KB may be a table, paragraph, list, heading, or mixed format — understand semantically.\n"
        "  5. Select a hotel ONLY if it satisfies ALL three: correct destination + exact category + present in KB.\n"
        "  6. If multiple hotels exist: pick one suitable option OR list them for user to choose.\n\n"

        "### E. CATEGORY NOT AVAILABLE\n"
        "If the requested category does not exist for a destination in the KB:\n"
        "  Tell the user: '[Category] is not available for [Destination]. Available categories are: [list]. Please select one.'\n"
        "  NEVER silently downgrade, upgrade, or substitute another category.\n\n"

        "### F. HOTEL NAME RULES\n"
        "  - Use EXACT hotel names from the KB. NEVER invent names.\n"
        "  - If KB says 'Hotel A / Hotel B / Similar', pick Hotel A or Hotel B, or list both.\n"
        "  - Preserve the word 'Similar' from KB if no specific hotel was chosen — do not replace with made-up name.\n\n"

        "### G. VALIDATION BEFORE OUTPUT\n"
        "Before inserting any hotel into the itinerary, confirm:\n"
        "  ✓ Hotel belongs to the requested destination\n"
        "  ✓ Hotel belongs to the exact requested category\n"
        "  ✓ Hotel is present in the KB\n"
        "  If any check fails: search KB again. Do not use the hotel.\n\n"

        "### H. BLOCK GENERATION UNTIL CONFIRMED\n"
        "Do NOT generate ---READY--- or ---JSON--- until:\n"
        "  - Hotel category is confirmed by user\n"
        "  - All destinations have a valid hotel from KB\n\n"


        "## RULES\n"
        "- Self-drive inclusion ONLY if itinerary has self-drive\n"
        "- Train tickets ONLY if trains are in this itinerary\n"
        "- Price from KB only; omit field if not available\n"
        "- Do NOT generate ---JSON--- until hotel category is confirmed AND all other selections confirmed\n\n"

        f"## KNOWLEDGE BASE\n{context}\n\n"
        f"## CONVERSATION HISTORY\n{history_text}"
    )

    raw = call_gemini(api_key, model, system, question)

    # Split answer and JSON
    meta = {}
    answer = raw

    # Detect ready separator (---READY--- preferred, ---JSON--- legacy fallback)
    for sep in ("---READY---", "---JSON---"):
        if sep in raw:
            parts = raw.split(sep, 1)
            answer = parts[0].strip()
            json_str = parts[1].strip()
            json_str = re.sub(r'^```[a-z]*\n?', '', json_str).strip()
            json_str = re.sub(r'\n?```$', '', json_str).strip()
            try:
                meta = json.loads(json_str)
            except Exception:
                m = re.search(r'\{.*\}', json_str, re.DOTALL)
                if m:
                    try:
                        meta = json.loads(m.group())
                    except Exception:
                        pass
            break

    if not meta:
        # Fallback: look for a JSON code-block anywhere in the response
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if not m:
            m = re.search(r'(\{\s*"destination".*?"notes".*?\})', raw, re.DOTALL)
        if m:
            try:
                meta = json.loads(m.group(1))
                answer = raw[:m.start()].strip()
                if not answer:
                    answer = f"Here is your itinerary for **{meta.get('destination','')}**."
            except Exception:
                pass

    return answer, sources, meta


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("✈ Travel Agent")
    st.caption("Powered by Gemini + ChromaDB")

    _default_key = (
        st.secrets.get("GOOGLE_API_KEY", "")
        if hasattr(st, "secrets") else ""
    ) or os.environ.get("GOOGLE_API_KEY", "")
    api_key = st.text_input(
        "Google Gemini API Key", type="password",
        value=_default_key,
        help="Free key from https://aistudio.google.com — or set GOOGLE_API_KEY in Streamlit secrets.",
    )
    model = st.selectbox(
        "Model",
        ["gemini-3.1-flash-lite", "gemini-flash-latest", "gemini-3.6-flash"],
        help="gemini-3.1-flash-lite is free.",
    )

    st.divider()
    st.subheader("📂 Travel Documents")
    uploaded = st.file_uploader("Upload PDF or Word files", type=["pdf", "docx"], accept_multiple_files=True)
    if uploaded:
        DOCS_DIR.mkdir(exist_ok=True)
        for f in uploaded:
            (DOCS_DIR / f.name).write_bytes(f.read())
        st.success(f"Saved {len(uploaded)} file(s)")
        if CHROMA_DIR.exists():
            shutil.rmtree(CHROMA_DIR)
        st.cache_resource.clear()
        st.session_state.pop("messages", None)

    existing = (list(DOCS_DIR.glob("*.pdf")) + list(DOCS_DIR.glob("*.docx"))) if DOCS_DIR.exists() else []
    if existing:
        st.divider()
        st.subheader("📄 Indexed Documents")
        for f in sorted(existing):
            st.write(f"• {f.name}")

    st.divider()
    if st.button("🔄 Re-index Documents", use_container_width=True):
        if CHROMA_DIR.exists():
            shutil.rmtree(CHROMA_DIR)
        st.cache_resource.clear()
        st.session_state.pop("messages", None)
        st.rerun()

    # History
    st.divider()
    st.subheader("🕘 Previous Chats")
    st.caption(f"Saved for {RETENTION} days")
    col_new, col_clear = st.columns(2)
    with col_new:
        if st.button("➕ New Chat", use_container_width=True):
            st.session_state.session_id = new_sid()
            st.session_state.messages = []
            st.rerun()
    with col_clear:
        if st.button("🗑️ Clear All", use_container_width=True):
            if HISTORY_DIR.exists():
                shutil.rmtree(HISTORY_DIR)
            HISTORY_DIR.mkdir(exist_ok=True)
            st.session_state.session_id = new_sid()
            st.session_state.messages = []
            st.rerun()

    for sf in list_sessions()[:15]:
        label = sf.stem.replace("_", " ", 1)
        if st.button(f"📂 {label}", key=str(sf), use_container_width=True):
            st.session_state.messages = load_session(sf)
            st.session_state.session_id = sf.stem
            st.rerun()


# ── Main ──────────────────────────────────────────────────────────────────────

st.title("✈ Travel Itinerary Agent")
st.caption("Ask with dates & travelers — get a formatted itinerary + PDF instantly.")

if not api_key:
    st.warning("Enter your **Google Gemini API key** in the sidebar. Free at https://aistudio.google.com")
    st.stop()

if not existing:
    st.info("Upload travel documents (PDF/DOCX) in the sidebar to get started.")
    st.stop()

with st.spinner("Loading document index…"):
    try:
        col = build_vectorstore()
    except Exception as e:
        st.error(f"Failed to build index: {e}")
        st.stop()

if col is None:
    st.info("No documents indexed yet — upload files in the sidebar.")
    st.stop()

st.success(f"✅ Indexed **{col.count()}** chunks from {len(existing)} file(s). Ready!")

if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content":
         "Hi! Tell me your travel dates, number of travelers, and destination — "
         "I'll create a personalised itinerary using your documents and generate a PDF! 🗺️"}
    ]

# Find the index of the most recent message with a PDF
_latest_pdf_idx = -1
for _i, _m in enumerate(st.session_state.messages):
    if _m.get("pdf"):
        _latest_pdf_idx = _i

for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # Only show download button on the most recent PDF message
        if msg.get("pdf") and idx == _latest_pdf_idx:
            st.download_button(
                "⬇️ Download Itinerary PDF",
                data=bytes.fromhex(msg["pdf"]),
                file_name=msg.get("pdf_name", "itinerary.pdf"),
                mime="application/pdf",
                key=f"dl_{msg.get('_id', id(msg))}",
            )

if prompt := st.chat_input("e.g. Plan a 5-day Georgia trip for 2 adults + 1 kid, 20-25 Aug 2026"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Gemini is crafting your itinerary…"):
            try:
                answer, sources, meta = get_answer(
                    col, prompt, st.session_state.messages[:-1], api_key, model
                )
                if sources:
                    answer += f"\n\n---\n*📄 Sources: {', '.join(sorted(sources))}*"
            except Exception as e:
                answer = f"❌ Error: {e}"
                meta = {}

        # When meta present, store as pending — do NOT auto-generate PDF
        if meta and not answer.startswith("❌"):
            st.session_state.pending_meta = meta
            st.session_state.pdf_ready = True
        elif not answer.startswith("❌"):
            # A clarification/question reply — itinerary not yet ready
            st.session_state.pdf_ready = False

        msg_entry = {
            "role": "assistant",
            "content": answer,
            "_id": len(st.session_state.messages),
        }
        if meta:
            msg_entry["meta"] = meta

        st.session_state.messages.append(msg_entry)
        save_session(st.session_state.session_id, st.session_state.messages)
        st.rerun()


# ── Itinerary Ready Card + Generate PDF Button ────────────────────────────────
if st.session_state.get("pdf_ready") and st.session_state.get("pending_meta"):
    meta_stored = st.session_state.pending_meta
    pkg_label = meta_stored.get("package_name") or meta_stored.get("destination") or "Itinerary"

    with st.container(border=True):
        st.success(f"✅ **Itinerary Ready** — {pkg_label}")
        st.caption("All required information has been confirmed. Click below to generate your PDF.")

        # Show persistent image fetch debug info (survives rerun)
        if st.session_state.get("_img_dbg_msgs"):
            for _m in st.session_state["_img_dbg_msgs"]:
                st.caption(f"🖼️ {_m}")

        if st.button("📄 Generate PDF", type="primary", use_container_width=True):
            with st.spinner("Generating PDF — fetching destination image…"):
                try:
                    img_kw  = meta_stored.get("image_keyword") or meta_stored.get("destination") or "travel landscape"
                    _img_dbg: list = []
                    hdr_img = fetch_destination_image(img_kw, _dbg=_img_dbg)
                    # If specific keyword fails, try just the destination country
                    if hdr_img is None:
                        fallback_kw = meta_stored.get("destination", "travel landscape")
                        if fallback_kw.lower() != img_kw.lower():
                            hdr_img = fetch_destination_image(fallback_kw, _dbg=_img_dbg)
                    st.session_state["_img_dbg_msgs"] = _img_dbg  # persist across rerun
                    pdf_b = generate_pdf(pkg_label, "", meta_stored, header_img_path=hdr_img)
                    pdf_n = f"itinerary_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
                    # Save PDF to template dir
                    TEMPLATE_DIR.mkdir(exist_ok=True)
                    (TEMPLATE_DIR / pdf_n).write_bytes(pdf_b)
                    # Store in last assistant message
                    for i in range(len(st.session_state.messages) - 1, -1, -1):
                        if st.session_state.messages[i].get("role") == "assistant":
                            st.session_state.messages[i]["pdf"]      = pdf_b.hex()
                            st.session_state.messages[i]["pdf_name"] = pdf_n
                            break
                    # Append image debug info to last assistant message for visibility
                    if _img_dbg:
                        img_note = "\n\n*🖼️ Image: " + " | ".join(_img_dbg) + "*"
                        for i in range(len(st.session_state.messages) - 1, -1, -1):
                            if st.session_state.messages[i].get("role") == "assistant":
                                st.session_state.messages[i]["content"] += img_note
                                break
                    save_session(st.session_state.session_id, st.session_state.messages)
                    st.session_state.pdf_ready = False
                    st.session_state.last_pdf       = pdf_b
                    st.session_state.last_pdf_name  = pdf_n
                    st.rerun()
                except Exception as e:
                    st.error(f"PDF generation failed: {e}")

