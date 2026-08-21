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


def _extract_amount_value(amount_str: str) -> float | None:
    """Extract the first numeric value from a price string, ignoring currency symbols/commas."""
    if not amount_str:
        return None
    m = re.search(r'[\d,]+(?:\.\d+)?', str(amount_str))
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except Exception:
        return None


def _extract_persons_count(persons_str: str) -> int:
    """Extract number of adults/persons from a string like '4 Adults · 1 Room'."""
    try:
        m = re.search(r'(\d+)\s*[Aa]dult', persons_str or "")
        if m:
            return max(1, int(m.group(1)))
    except Exception:
        pass
    return 1


def _is_usd_amount(amount_str: str) -> bool:
    """Detect whether a price string is denominated in USD."""
    return bool(re.search(r'USD|US\$|\$', str(amount_str or ""), re.I))


def _is_per_person_amount(amount_str: str) -> bool:
    """Detect whether a price string is denominated per person (vs. total package)."""
    return bool(re.search(r'per\s*person|per\s*pax|/\s*person|/\s*pax|\bpp\b',
                          str(amount_str or ""), re.I))



def _format_inr(amount: float) -> str:
    """Format a number using Indian digit grouping, e.g. 400000 -> '4,00,000'."""
    n = int(round(amount))
    sign = "-" if n < 0 else ""
    n = abs(n)
    s = str(n)
    if len(s) <= 3:
        return sign + s
    last3 = s[-3:]
    rest = s[:-3]
    parts = []
    while len(rest) > 2:
        parts.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.insert(0, rest)
    return sign + ",".join(parts) + "," + last3


def _parse_markup(markup_raw: str, base_amount: float) -> float:
    """Parse a markup string (percentage or fixed amount) and return the markup value in INR."""
    markup_raw = (markup_raw or "").strip()
    if "%" in markup_raw:
        m = re.search(r'[\d.]+', markup_raw)
        pct = float(m.group()) if m else 0.0
        return base_amount * (pct / 100.0)
    val = _extract_amount_value(markup_raw)
    return val or 0.0




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

    # ── Embed company logo (coollogo.png) ─────────────────────────────────────
    logo_tag = ""
    logo_path = _APP_DIR / "template" / "coollogo.png"
    if logo_path.exists():
        logo_b64 = base64.b64encode(logo_path.read_bytes()).decode()
        logo_tag = (
            f'<img class="hero-logo" src="data:image/png;base64,{logo_b64}" />'
        )

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

    # ── Final customer-facing price ─────────────────────────────────────────
    # If a finalized price has been computed via the pricing workflow
    # (_final_price_label / _final_price_value), use that exclusively.
    # Never fall back to showing raw/base/USD amounts on the customer PDF.
    final_label = meta.get("_final_price_label", "")
    final_value = meta.get("_final_price_value", "")

    if final_value:
        price_label_text = final_label or "TOTAL PACKAGE COST"
        amount_display = final_value
    else:
        # Price workflow not yet run — generate a reasonable placeholder
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
        amount_display = f"INR {placeholder_amt:,}/- (approx.)"
        price_label_text = "TOTAL PACKAGE COST"

    # ── Package price HTML (uses computed amount with fallback) ────────────────
    persons_rooms = "   ·   ".join(filter(None, [
        meta.get("persons", ""), meta.get("transport", "")]))
    price_html = f"""
        <div class="price-card">
          <div class="price-label">{_h(price_label_text.upper())}</div>
          <div class="price">{_h(str(amount_display))}</div>
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
          <div class="notes-title">📌 Important Notes</div>
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
.hero-logo {{
    display: block;
    margin: 0 auto 10px auto;
    height: 64px;
    width: auto;
    max-width: 320px;
    object-fit: contain;
    opacity: 0.95;
    filter: drop-shadow(0 1px 3px rgba(0,0,0,0.45));
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
      {logo_tag}
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
    """
    Extract text from a DOCX in TRUE READING ORDER, interleaving paragraphs
    and tables exactly as they appear in the document body.

    This is critical for multi-package documents (e.g. several Georgia
    itinerary variants in one file) where each package has its own pricing
    table immediately following its package-name heading. Processing all
    paragraphs first and all tables afterward (the previous approach)
    completely disconnects each price table from its package heading,
    causing the LLM/retrieval to pull the wrong package's rate table.

    Each table row is also prefixed with the most recent package/section
    heading seen so far, so retrieved chunks always carry enough context
    to identify which package the rates/hotels belong to — even if the
    heading itself ends up in a different chunk after text splitting.
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    doc = Document(path)
    body = doc.element.body

    parts = []
    current_heading = ""
    # Headings are short, non-tabular lines that look like a package/section
    # title — e.g. "6 Nights - 4 Nights Tbilisi + 2 Nights Batumi".
    # IMPORTANT: generic lines like "Duration: 7 Days and 6 Nights" or
    # "Validity: April 1 to October 31, 2026" also mention Nights/Days but
    # are NOT unique package identifiers — they repeat verbatim across
    # multiple different packages in this document. If these overwrite
    # current_heading, every subsequent price table gets tagged with the
    # wrong (non-unique) package label, causing rate mix-ups between
    # packages. Only the actual package-title line (which names the
    # cities/route, not just a generic duration/validity statement) should
    # update current_heading.
    heading_re = re.compile(r'\bNights?\b|\bDays?\b', re.I)
    day_re = re.compile(r'^\s*Day\s*\d+\s*:', re.I)
    non_title_re = re.compile(
        r'^\s*(Duration|Validity|Cities|Rates|Accommodation|Supplement|'
        r'No\.?\s*of\s*Pax|Destination)\s*[:\-]', re.I
    )

    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            p = Paragraph(child, doc)
            text = p.text.strip()
            if not text:
                continue
            parts.append(text)
            # Update current package heading — prefer lines like
            # "6 Nights - 4 Nights Tbilisi + 2 Nights Batumi" over
            # per-day headings like "Day 1: Arrival" or generic
            # "Duration: ..." / "Validity: ..." lines that repeat
            # identically across many different packages.
            if (heading_re.search(text) and not day_re.match(text)
                    and not non_title_re.match(text) and len(text) < 120):
                current_heading = text

        elif child.tag == qn('w:tbl'):
            table = Table(child, doc)
            if not table.rows:
                continue
            headers = [c.text.strip() for c in table.rows[0].cells]
            for row in table.rows[1:]:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if not any(cells):
                    continue
                if headers:
                    row_text = " | ".join(
                        f"{h}: {v}" for h, v in zip(headers, cells) if v
                    )
                else:
                    row_text = " | ".join(c for c in cells if c)
                if current_heading:
                    row_text = f"[Package: {current_heading}] {row_text}"
                parts.append(row_text)

    return "\n".join(parts)


def extract_all_price_tables(docs_dir: Path) -> str:
    """
    Extract EVERY price/rate table from every DOCX in docs_dir, tagged with
    its package heading, and return them concatenated as a single block.

    WHY THIS EXISTS: with 6+ near-identical pricing tables in one document
    (same headers "No of Pax | 5 Star | 4 Star... | 3 Star", same row
    labels "2 Pax / 4 Pax / 6 Pax"), semantic vector similarity search
    cannot reliably distinguish which table belongs to which package —
    it may retrieve only PART of a table, or rows from the wrong package,
    since the embeddings for two different packages' price rows are nearly
    identical. This caused wrong USD rates to be quoted (e.g. picking the
    "4 Nights Tbilisi" table's numbers for the "6 Nights - 4 Nights Tbilisi
    + 2 Nights Batumi" package).

    FIX: bypass retrieval entirely for pricing. Extract the complete,
    authoritative set of price tables directly from the source documents
    (already tagged with unique package headings by load_docx_text's
    reading-order walk) and inject the FULL, UNTRUNCATED block into every
    LLM call's context. This guarantees the model always sees the
    complete, correctly-labeled table for every package, rather than a
    similarity-searched fragment that might belong to a different package.
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    heading_re = re.compile(r'\bNights?\b|\bDays?\b', re.I)
    day_re = re.compile(r'^\s*Day\s*\d+\s*:', re.I)
    non_title_re = re.compile(
        r'^\s*(Duration|Validity|Cities|Rates|Accommodation|Supplement|'
        r'No\.?\s*of\s*Pax|Destination)\s*[:\-]', re.I
    )
    # Only tables whose header row mentions Pax/Star/Cost are pricing tables
    price_header_re = re.compile(r'Pax|Star|Cost|Rate|Price|USD|INR', re.I)

    blocks = []
    for f in sorted(Path(docs_dir).glob("*.docx")):
        try:
            doc = Document(f)
        except Exception:
            continue
        body = doc.element.body
        current_heading = ""
        for child in body.iterchildren():
            if child.tag == qn('w:p'):
                p = Paragraph(child, doc)
                text = p.text.strip()
                if not text:
                    continue
                if (heading_re.search(text) and not day_re.match(text)
                        and not non_title_re.match(text) and len(text) < 120):
                    current_heading = text
            elif child.tag == qn('w:tbl'):
                table = Table(child, doc)
                if not table.rows:
                    continue
                headers = [c.text.strip() for c in table.rows[0].cells]
                header_line = " ".join(headers)
                if not price_header_re.search(header_line):
                    continue  # skip non-pricing tables (e.g. hotel-name tables)
                rows_text = []
                for row in table.rows[1:]:
                    cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                    if not any(cells):
                        continue
                    row_text = " | ".join(
                        f"{h}: {v}" for h, v in zip(headers, cells) if v
                    )
                    rows_text.append(row_text)
                if rows_text and current_heading:
                    blocks.append(
                        f"### PRICE TABLE — Package: {current_heading} (source: {f.name})\n"
                        + "\n".join(rows_text)
                    )
    return "\n\n".join(blocks)


def extract_all_inclusion_exclusion_tables(docs_dir: Path) -> str:
    """
    Extract EVERY Inclusions/Exclusions table from every DOCX in docs_dir,
    tagged with its package heading, and return them concatenated as a
    single authoritative block.

    WHY THIS EXISTS: exactly the same problem as extract_all_price_tables —
    each package (e.g. "5 Nights - 4 Nights Tbilisi + 1 Night Gudauri" vs
    "6 Nights - 4 Nights Tbilisi + 2 Nights Batumi") has its OWN two-column
    "Inclusions | Exclusions" table with nights/city-specific line items
    (e.g. "3 Nights' accommodation in Tbilisi", "1 Night accommodation in
    Gudauri"), but the wording overlaps heavily across packages ("Daily
    Breakfast", "Round Airport Transfers", "Tips for guide and driver" appear
    almost everywhere). Semantic similarity search cannot reliably tell which
    package's table a retrieved chunk belongs to, so it can quote the wrong
    package's night-by-night accommodation breakdown.

    FIX: bypass retrieval for inclusions/exclusions entirely. Extract the
    complete, correctly-labeled table for every package directly from the
    source documents (matching each table to the most recent package
    heading, exactly like price tables) and inject the full block into every
    LLM call's context.
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    heading_re = re.compile(r'\bNights?\b|\bDays?\b', re.I)
    day_re = re.compile(r'^\s*Day\s*\d+\s*:', re.I)
    non_title_re = re.compile(
        r'^\s*(Duration|Validity|Cities|Rates|Accommodation|Supplement|'
        r'No\.?\s*of\s*Pax|Destination)\s*[:\-]', re.I
    )

    blocks = []
    for f in sorted(Path(docs_dir).glob("*.docx")):
        try:
            doc = Document(f)
        except Exception:
            continue
        body = doc.element.body
        current_heading = ""
        for child in body.iterchildren():
            if child.tag == qn('w:p'):
                p = Paragraph(child, doc)
                text = p.text.strip()
                if not text:
                    continue
                if (heading_re.search(text) and not day_re.match(text)
                        and not non_title_re.match(text) and len(text) < 120):
                    current_heading = text
            elif child.tag == qn('w:tbl'):
                table = Table(child, doc)
                if not table.rows:
                    continue
                headers = [c.text.strip() for c in table.rows[0].cells]
                header_line = " ".join(headers).lower()
                # Only the 2-column "Inclusions | Exclusions" table
                if "inclusion" not in header_line or "exclusion" not in header_line:
                    continue
                inc_items, exc_items = [], []
                for row in table.rows[1:]:
                    cells = row.cells
                    if len(cells) >= 2:
                        inc_items += [
                            ln.strip(" \u00a0\u2022-")
                            for ln in cells[0].text.replace("\u00a0", " ").split("\n")
                            if ln.strip(" \u00a0\u2022-")
                        ]
                        exc_items += [
                            ln.strip(" \u00a0\u2022-")
                            for ln in cells[1].text.replace("\u00a0", " ").split("\n")
                            if ln.strip(" \u00a0\u2022-")
                        ]
                if (inc_items or exc_items) and current_heading:
                    block = (
                        f"### INCLUSIONS & EXCLUSIONS — Package: {current_heading} "
                        f"(source: {f.name})\n"
                        "Inclusions:\n" + "\n".join(f"- {i}" for i in inc_items) + "\n"
                        "Exclusions:\n" + "\n".join(f"- {e}" for e in exc_items)
                    )
                    blocks.append(block)
    return "\n\n".join(blocks)


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

    # ── Always inject the COMPLETE, authoritative price tables ──────────────
    # Semantic retrieval alone is unreliable for pricing data: when a
    # document contains multiple near-identical price tables (same headers,
    # same "2 Pax / 4 Pax / 6 Pax" row labels, differing only in the numeric
    # values), vector similarity search can retrieve a fragment or the WRONG
    # package's table into context, causing the LLM to quote incorrect
    # rates. To guarantee correctness, bypass retrieval entirely for price
    # tables and inject the full, correctly-labeled set directly.
    price_tables_block = extract_all_price_tables(DOCS_DIR)
    if price_tables_block:
        context += (
            "\n\n## AUTHORITATIVE PRICE TABLES (use these EXACTLY — "
            "do not use price figures found elsewhere in the context above; "
            "each table is labeled with its exact package name — match the "
            "package name precisely before using its rates)\n"
            + price_tables_block
        )

    # ── Always inject the COMPLETE, authoritative inclusions/exclusions ─────
    # Same rationale as price tables: each package's nights/city-specific
    # inclusion & exclusion list (e.g. "3 Nights' accommodation in Tbilisi",
    # "1 Night accommodation in Gudauri") is easily confused with a
    # different package's list by semantic retrieval, since the wording
    # overlaps heavily ("Daily Breakfast", "Round Airport Transfers", etc.
    # appear in nearly every package). Bypass retrieval and inject the full,
    # correctly-labeled set directly so the model always matches the exact
    # package name before using its inclusions/exclusions.
    incl_excl_block = extract_all_inclusion_exclusion_tables(DOCS_DIR)
    if incl_excl_block:
        context += (
            "\n\n## AUTHORITATIVE INCLUSIONS & EXCLUSIONS (use these EXACTLY — "
            "do not use inclusion/exclusion items found elsewhere in the context "
            "above; each block is labeled with its exact package name — match "
            "the package name/nights-breakdown precisely (e.g. '5 Nights - 4 "
            "Nights Tbilisi + 1 Night Gudauri' is a DIFFERENT package from '6 "
            "Nights - 4 Nights Tbilisi + 2 Nights Batumi') before using its "
            "inclusions/exclusions — do NOT mix items from a different "
            "package's list even if the wording looks similar)\n"
            + incl_excl_block
        )



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

        "## INCLUSIONS & EXCLUSIONS SELECTION — PACKAGE-EXACT MATCH (MANDATORY)\n"
        "The '## AUTHORITATIVE INCLUSIONS & EXCLUSIONS' section below contains a SEPARATE "
        "inclusions/exclusions list for EVERY package variant in the knowledge base. Many "
        "packages share the same cities and nearly identical wording (e.g. 'Daily Breakfast', "
        "'Round Airport Transfers', 'Tips for guide and driver' appear in almost every package), "
        "but the NIGHT-BY-NIGHT accommodation breakdown is UNIQUE to each package and MUST match "
        "exactly the itinerary you are building. For example:\n"
        "  - '5 Nights - 4 Nights Tbilisi + 1 Night Gudauri' → inclusions list '3 Nights' "
        "accommodation in Tbilisi', '1 Night accommodation in Gudauri', '1 Night accommodation "
        "in Tbilisi' (i.e. split before/after the Gudauri night)\n"
        "  - '6 Nights - 4 Nights Tbilisi + 2 Nights Batumi' → a COMPLETELY DIFFERENT inclusions "
        "list with Batumi nights instead of Gudauri\n"
        "These must NEVER be mixed, even though most of the other bullet points look identical.\n\n"
        "STEP-BY-STEP:\n"
        "  1. Identify the EXACT package heading that matches the itinerary's total nights and "
        "the exact city + night split (e.g. '5 Nights - 4 Nights Tbilisi + 1 Night Gudauri').\n"
        "  2. Locate the block in '## AUTHORITATIVE INCLUSIONS & EXCLUSIONS' whose 'Package:' "
        "label matches that heading exactly (or as close as possible if the itinerary was "
        "custom-modified from a base package).\n"
        "  3. Copy that block's Inclusions and Exclusions items verbatim into the JSON "
        "'inclusions' and 'exclusions' arrays.\n"
        "  4. Do NOT invent, merge, or borrow any accommodation-night line item from a "
        "different package's block — the night count and city split MUST reconcile with the "
        "itinerary's actual day-by-day overnight cities.\n"
        "  5. If no exact match exists (e.g. a fully custom itinerary), select the CLOSEST "
        "matching package by nights/cities and adapt only the accommodation-night lines to "
        "reflect the actual itinerary, keeping all other generic inclusions/exclusions intact.\n\n"


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

# col.count() can fail if persisted DB schema is stale — auto-rebuild if so
try:
    chunk_count = col.count()
except Exception:
    # Stale/incompatible DB — wipe and rebuild
    if CHROMA_DIR.exists():
        shutil.rmtree(CHROMA_DIR)
    st.cache_resource.clear()
    st.rerun()

st.success(f"✅ Indexed **{chunk_count}** chunks from {len(existing)} file(s). Ready!")

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
            # Reset pricing workflow state for the new itinerary
            for _k in ("price_conv_rate", "price_display_mode", "price_markup_raw",
                       "price_workflow_done", "final_price_label", "final_price_value"):
                st.session_state.pop(_k, None)
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


# ── Itinerary Ready Card + Pricing Workflow + Generate PDF Button ─────────────
if st.session_state.get("pdf_ready") and st.session_state.get("pending_meta"):
    meta_stored = st.session_state.pending_meta
    pkg_label = meta_stored.get("package_name") or meta_stored.get("destination") or "Itinerary"

    raw_amount = meta_stored.get("amount", "")
    base_value = _extract_amount_value(raw_amount)
    is_usd = _is_usd_amount(raw_amount)
    is_per_person = _is_per_person_amount(raw_amount) if raw_amount else None
    persons_count = _extract_persons_count(meta_stored.get("persons", ""))


    with st.container(border=True):
        st.success(f"✅ **Itinerary Ready** — {pkg_label}")
        st.caption("Complete the pricing steps below, then generate your PDF.")

        # Show persistent image fetch debug info (survives rerun)
        if st.session_state.get("_img_dbg_msgs"):
            for _m in st.session_state["_img_dbg_msgs"]:
                st.caption(f"🖼️ {_m}")

        # Show the original base price (as found in the KB) for the agent's
        # reference only — this is NEVER shown in the customer-facing PDF.
        if raw_amount:
            st.caption(f"💰 Base price from knowledge base (internal use only): **{raw_amount}**")

        if base_value is None:
            st.warning(
                "⚠️ No base package price was found in the knowledge base for this itinerary. "
                "Please provide a base price manually to continue."
            )

        # ── Manual base price override — always available, with INR/USD toggle ──
        # Lets the agent overwrite the knowledge-base price (or supply one when
        # none was found) in either currency. If left blank, the default base
        # price picked from the knowledge base (shown above) is used as-is.
        # When INR is chosen (default or override), the USD→INR conversion
        # step is skipped entirely; when USD is chosen, conversion is required.
        with st.expander(
            "✏️ Override base price (optional)" if base_value is not None else "✏️ Enter base price",
            expanded=(base_value is None),
        ):
            if base_value is not None:
                st.caption(
                    "Leave the field below blank to keep using the default base price "
                    f"from the knowledge base ({raw_amount})."
                )
            override_currency = st.radio(
                "Currency of the price you're entering",
                ["USD", "INR"],
                index=0 if is_usd else 1,   # default to the currency already detected from the KB
                key="price_override_currency_input",
                horizontal=True,
            )
            manual_amount = st.text_input(
                f"Base package price in {override_currency} (numbers only, no currency symbol) — "
                "leave blank to use the default",
                key="price_manual_base",
            )
            manual_val = _extract_amount_value(manual_amount)
            if manual_val:
                # User explicitly overrode the price — use their value + currency.
                base_value = manual_val
                is_usd = (override_currency == "USD")
            # else: no override entered — base_value/is_usd remain the KB defaults



        # ── Confirm price basis (per-person vs total package) — always shown,
        # defaulted from detection but user-confirmable to avoid ambiguity. ──
        if base_value is not None:
            basis_default_idx = 0 if is_per_person else 1
            basis_choice = st.radio(
                "Confirm: is the base price above per person or total package price?",
                ["Per Person", "Total Package"],
                index=basis_default_idx,
                key="price_basis_confirm_input",
                horizontal=True,
            )
            is_per_person = (basis_choice == "Per Person")

        conv_rate = None
        if base_value is not None and is_usd:
            st.markdown("**Step 1 — USD → INR Conversion Rate**")
            conv_rate_raw = st.text_input(
                "What is the USD → INR conversion rate to use?",
                key="price_conv_rate_input",
                placeholder="e.g. 84.5",
            )
            conv_rate = _extract_amount_value(conv_rate_raw)
            if conv_rate_raw and not conv_rate:
                st.error("Please enter a valid numeric conversion rate.")

        # Base price in INR (after conversion if needed) — remains on the
        # SAME basis (per-person or total) as the original amount; this basis
        # carries through agent markup and markup, and is only resolved to
        # the user's chosen display format in the final step.
        base_inr = None
        if base_value is not None:
            if is_usd:
                if conv_rate:
                    base_inr = base_value * conv_rate
            else:
                base_inr = base_value


        agent_markup_raw = None
        if base_inr is not None:
            st.markdown("**Step 2 — Agent Markup (internal, percentage only)**")
            agent_markup_raw = st.text_input(
                "What is your agent markup percentage, applied on the converted total? "
                "(internal use only — never shown to the customer or in the PDF)",
                key="price_agent_markup_input",
                placeholder="e.g. 10%",
            )

        # ── Compute base (after conversion) + agent markup (internal only) ──
        base_after_agent_markup = None
        agent_markup_val = None
        if base_inr is not None and agent_markup_raw:
            m = re.search(r'[\d.]+', agent_markup_raw)
            agent_pct = float(m.group()) if m else 0.0
            agent_markup_val = base_inr * (agent_pct / 100.0)
            base_after_agent_markup = base_inr + agent_markup_val
            st.caption(
                f"🧮 Internal calculation (not shown to customer): "
                f"Base ₹{_format_inr(base_inr)} + Agent Markup {agent_pct}% "
                f"(₹{_format_inr(agent_markup_val)}) = ₹{_format_inr(base_after_agent_markup)}"
            )

        markup_raw = None
        if base_after_agent_markup is not None:
            st.markdown("**Step 3 — Markup**")
            markup_raw = st.text_input(
                "What markup would you like to add to the package price? "
                "(percentage e.g. 15% or fixed amount e.g. 20000)",
                key="price_markup_input",
                placeholder="e.g. 15% or 20000",
            )

        # ── Total after customer markup (internal, before display split) ────
        total_final = None
        if base_after_agent_markup is not None and markup_raw:
            markup_val = _parse_markup(markup_raw, base_after_agent_markup)
            total_final = base_after_agent_markup + markup_val

        display_mode = None
        if total_final is not None:
            st.markdown("**Step 4 — Price Display in PDF**")
            display_mode = st.radio(
                "How would you like the price to be shown in the PDF?",
                ["Price Per Person", "Total Package Price"],
                key="price_display_mode_input",
                horizontal=True,
            )

        # ── Compute final customer-facing price ─────────────────────────────
        # `total_final` is on the SAME basis as the original base amount
        # (per-person or total package), tracked via `is_per_person`.
        # Convert to whichever basis the user wants displayed, without
        # ever double-dividing/multiplying by persons_count.
        final_label = None
        final_value_str = None
        ready_to_generate = False

        if total_final is not None and display_mode:
            if is_per_person:
                per_person_amt = total_final
                total_amt = total_final * max(1, persons_count)
            else:
                total_amt = total_final
                per_person_amt = total_final / max(1, persons_count)

            if display_mode == "Price Per Person":
                final_label = "Price Per Person"
                final_value_str = f"₹{_format_inr(per_person_amt)} / person"
            else:
                final_label = "Total Package Price"
                final_value_str = f"₹{_format_inr(total_amt)}"
            ready_to_generate = True


        if ready_to_generate:
            st.info(f"**{final_label}:** {final_value_str}")

        gen_disabled = not ready_to_generate
        if gen_disabled:
            st.caption(
                "⏳ Complete all pricing steps above (conversion rate if applicable, "
                "price display option, and markup) before generating the PDF."
            )

        if st.button("📄 Generate PDF", type="primary", use_container_width=True, disabled=gen_disabled):
            with st.spinner("Generating PDF — fetching destination image…"):
                try:
                    # Inject only the final computed price into meta — never the
                    # base/USD/markup/conversion details, per pricing policy.
                    meta_for_pdf = dict(meta_stored)
                    meta_for_pdf["_final_price_label"] = final_label
                    meta_for_pdf["_final_price_value"] = final_value_str

                    img_kw  = meta_stored.get("image_keyword") or meta_stored.get("destination") or "travel landscape"
                    _img_dbg: list = []
                    hdr_img = fetch_destination_image(img_kw, _dbg=_img_dbg)
                    # If specific keyword fails, try just the destination country
                    if hdr_img is None:
                        fallback_kw = meta_stored.get("destination", "travel landscape")
                        if fallback_kw.lower() != img_kw.lower():
                            hdr_img = fetch_destination_image(fallback_kw, _dbg=_img_dbg)
                    st.session_state["_img_dbg_msgs"] = _img_dbg  # persist across rerun
                    pdf_b = generate_pdf(pkg_label, "", meta_for_pdf, header_img_path=hdr_img)
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

