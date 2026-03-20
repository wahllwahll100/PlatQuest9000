"""
PlatQuest 9000 — Orange County FL Plat Lookup

Enter an address. Get the plat PDF. That's it.

Human workflow this replicates:
  1. Go to OCPA, search address
  2. Click the Plats tab
  3. Click "Continue to site" in the dropdown
  4. Download the plat PDF from the Comptroller page
"""

import streamlit as st
import subprocess
import re
import base64

st.set_page_config(page_title="PlatQuest 9000", page_icon="📄", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap');
.stApp { font-family: 'Space Grotesk', sans-serif; }
.app-header {
    background: linear-gradient(135deg, #0c1b2a 0%, #1a365d 100%);
    color: white; padding: 2rem; border-radius: 12px; margin-bottom: 1.5rem;
    text-align: center;
}
.app-header h1 { font-size: 2.2rem; font-weight: 700; margin: 0; }
.app-header p { color: #94a3b8; margin: 0.5rem 0 0; font-size: 1rem; }
.step { padding: 10px 14px; border-radius: 8px; margin: 6px 0; font-size: 14px; }
.step-ok { background: #dcfce7; border-left: 4px solid #22c55e; }
.step-fail { background: #fee2e2; border-left: 4px solid #ef4444; }
.step-work { background: #dbeafe; border-left: 4px solid #3b82f6; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="app-header">
    <h1>📄 PlatQuest 9000</h1>
    <p>Orange County, FL — Enter address, get plat PDF</p>
</div>
""", unsafe_allow_html=True)


@st.cache_resource
def install_playwright():
    """Install Chromium once."""
    try:
        r = subprocess.run(["playwright", "install", "chromium"],
                           capture_output=True, text=True, timeout=120)
        return True, r.stdout + r.stderr
    except Exception as e:
        return False, str(e)


def find_plat_pdf(address):
    """
    Replicate exactly what a human does:
      1. Go to OCPA → search the address
      2. On the parcel page, click the Plats tab
      3. Find the "Continue to site" link → follow it to the Comptroller
      4. On the Comptroller page, capture the PDF from network traffic

    Returns: (pdf_bytes, comptroller_url, debug_log)
    """
    from playwright.sync_api import sync_playwright

    pdf_bytes = None
    comptroller_url = None
    debug = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ))

            # ═══════════════════════════════════════════════════
            # STEP 1: Search OCPA by address
            # ═══════════════════════════════════════════════════
            addr_encoded = address.strip().upper().replace(" ", "%20")
            search_url = f"https://ocpaweb.ocpafl.org/parcelsearch/Site%20Address/{addr_encoded}"
            debug.append(f"1. Opening OCPA: {search_url}")

            page.goto(search_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(4000)
            debug.append(f"   Landed on: {page.url}")
            debug.append(f"   Page title: {page.title()}")

            # If we got a search results list, click the first result
            current = page.url.lower()
            if "parcel%20id" not in current and "parcel id" not in current:
                debug.append("   → Looks like a results list, clicking first result...")
                try:
                    selectors = [
                        "a[href*='Parcel']",
                        "a[href*='parcel']",
                        "tr.cursor-pointer",
                        "div.search-result",
                        "table tbody tr",
                    ]
                    clicked = False
                    for sel in selectors:
                        els = page.query_selector_all(sel)
                        if els:
                            els[0].click()
                            page.wait_for_load_state("networkidle", timeout=15000)
                            page.wait_for_timeout(3000)
                            debug.append(f"   Clicked: {sel} → now at {page.url}")
                            clicked = True
                            break
                    if not clicked:
                        debug.append("   Could not find clickable result")
                except Exception as e:
                    debug.append(f"   Click error: {e}")

            debug.append(f"   On parcel page: {page.url}")

            # ═══════════════════════════════════════════════════
            # STEP 2: Find and click the Plats tab/section
            # ═══════════════════════════════════════════════════
            debug.append("2. Looking for Plats tab...")

            plat_clicked = False

            # Try exact text matches first
            for text in ["Plats", "Plat", "PLATS", "PLAT"]:
                try:
                    el = page.get_by_text(text, exact=True).first
                    if el and el.is_visible():
                        el.click()
                        page.wait_for_timeout(3000)
                        debug.append(f"   Clicked: '{text}'")
                        plat_clicked = True
                        break
                except Exception:
                    pass

            # Try partial match
            if not plat_clicked:
                for text in ["Plat", "plat"]:
                    try:
                        el = page.get_by_text(text, exact=False).first
                        if el and el.is_visible():
                            el.click()
                            page.wait_for_timeout(3000)
                            debug.append(f"   Clicked (partial): '{text}'")
                            plat_clicked = True
                            break
                    except Exception:
                        pass

            # Try any tab-like elements
            if not plat_clicked:
                tabs = page.query_selector_all("button, a, li, div[role='tab'], span")
                for tab in tabs:
                    try:
                        txt = tab.inner_text().strip().lower()
                        if "plat" in txt:
                            tab.click()
                            page.wait_for_timeout(3000)
                            debug.append(f"   Clicked tab element: '{txt}'")
                            plat_clicked = True
                            break
                    except Exception:
                        pass

            if not plat_clicked:
                debug.append("   ⚠ Could not find Plats tab — scanning full page")

            # ═══════════════════════════════════════════════════
            # STEP 3: Find the "Continue to site" / Comptroller link
            # ═══════════════════════════════════════════════════
            debug.append("3. Looking for Comptroller link...")

            # First try: click "Continue to site" or similar
            for btn_text in ["Continue to site", "Continue to Site", "Continue",
                             "View Document", "View Plat", "View", "Open"]:
                try:
                    btn = page.get_by_text(btn_text, exact=False).first
                    if btn and btn.is_visible():
                        href = btn.get_attribute("href") or ""
                        if href and ("occompt" in href or "selfservice" in href or "ssweb" in href):
                            comptroller_url = href if href.startswith("http") else f"https://ocpaweb.ocpafl.org{href}"
                            debug.append(f"   Found link: '{btn_text}' → {comptroller_url[:120]}")
                            break
                        else:
                            debug.append(f"   Clicking '{btn_text}'...")
                            btn.click()
                            page.wait_for_timeout(3000)
                            new_url = page.url
                            if "occompt" in new_url or "selfservice" in new_url:
                                comptroller_url = new_url
                                debug.append(f"   Navigated to: {new_url}")
                                break
                except Exception:
                    pass

            # Second try: scan all <a> tags for Comptroller URLs
            if not comptroller_url:
                all_links = page.query_selector_all("a")
                for link in all_links:
                    try:
                        href = link.get_attribute("href") or ""
                        if "occompt" in href or "selfservice" in href or "ssweb" in href:
                            comptroller_url = href if href.startswith("http") else f"https://selfservice.or.occompt.com{href}"
                            text = link.inner_text().strip()
                            debug.append(f"   Found <a> link: '{text}' → {comptroller_url[:120]}")
                            break
                    except Exception:
                        pass

            # Third try: scan raw HTML for Comptroller URLs
            if not comptroller_url:
                html = page.content()
                found_urls = re.findall(r'https?://[^"\'<>\s]*(?:occompt|selfservice)[^"\'<>\s]*', html, re.IGNORECASE)
                if found_urls:
                    comptroller_url = found_urls[0]
                    debug.append(f"   Found in HTML: {comptroller_url[:120]}")

            if not comptroller_url:
                debug.append("   ❌ No Comptroller links found")
                page_text = page.inner_text("body")[:2000]
                debug.append(f"   Page text:\n{page_text}")
                browser.close()
                return None, None, debug

            # ═══════════════════════════════════════════════════
            # STEP 4: Open Comptroller page and capture the PDF
            # ═══════════════════════════════════════════════════
            debug.append(f"4. Fetching PDF from: {comptroller_url}")

            captured_pdfs = []

            def on_response(response):
                try:
                    ct = response.headers.get("content-type", "").lower()
                    if "pdf" in ct or response.url.lower().endswith(".pdf"):
                        body = response.body()
                        if body and body[:4] == b'%PDF':
                            captured_pdfs.append(body)
                except Exception:
                    pass

            page.on("response", on_response)
            page.goto(comptroller_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(5000)

            if captured_pdfs:
                pdf_bytes = max(captured_pdfs, key=len)
                debug.append(f"   ✅ Captured PDF: {len(pdf_bytes):,} bytes")
            else:
                debug.append("   No PDF in network traffic")

                # Try iframes/embeds
                for sel in ["iframe", "embed", "object"]:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            src = el.get_attribute("src") or el.get_attribute("data") or ""
                            if src:
                                debug.append(f"   Found <{sel}>: {src[:100]}")
                                if not src.startswith("http"):
                                    src = f"https://selfservice.or.occompt.com{src}"
                                resp = page.context.request.get(src)
                                body = resp.body()
                                if body[:4] == b'%PDF':
                                    pdf_bytes = body
                                    debug.append(f"   ✅ Got PDF from <{sel}>: {len(pdf_bytes):,} bytes")
                                    break
                    except Exception:
                        pass

                # Try download buttons on Comptroller page
                if not pdf_bytes:
                    for btn_text in ["Download", "View", "Print", "PDF"]:
                        try:
                            btn = page.get_by_text(btn_text, exact=False).first
                            if btn and btn.is_visible():
                                with page.expect_download(timeout=10000) as dl:
                                    btn.click()
                                download = dl.value
                                import tempfile, os
                                tmp = tempfile.mktemp(suffix=".pdf")
                                download.save_as(tmp)
                                with open(tmp, "rb") as f:
                                    pdf_bytes = f.read()
                                os.unlink(tmp)
                                if pdf_bytes and pdf_bytes[:4] == b'%PDF':
                                    debug.append(f"   ✅ Got PDF from '{btn_text}': {len(pdf_bytes):,} bytes")
                                else:
                                    pdf_bytes = None
                                break
                        except Exception:
                            pass

                if not pdf_bytes:
                    chtml = page.content()[:2000]
                    debug.append(f"   Comptroller page HTML:\n{chtml}")

            browser.close()

    except Exception as e:
        debug.append(f"Error: {e}")

    return pdf_bytes, comptroller_url, debug


# ── UI ───────────────────────────────────────────────────────────────────────

address = st.text_input(
    "🏠 Enter an Orange County address:",
    placeholder="763 Golden Sunshine Cir",
)

col1, col2 = st.columns([1, 4])
with col1:
    go = st.button("🔍 Find Plat", use_container_width=True, type="primary")
with col2:
    st.caption("Searches OCPA → clicks Plats → follows link to Comptroller → downloads PDF")

if go and address:
    with st.spinner("⚙️ Preparing browser..."):
        pw_ok, pw_msg = install_playwright()
    if not pw_ok:
        st.error(f"Browser install failed: {pw_msg}")
        st.stop()

    with st.spinner(f"🔍 Finding plat for **{address}**... (takes ~20 seconds)"):
        pdf_bytes, comptroller_url, debug_log = find_plat_pdf(address)

    if pdf_bytes:
        st.markdown(f'<div class="step step-ok">✅ <strong>Plat PDF retrieved</strong> — {len(pdf_bytes):,} bytes</div>', unsafe_allow_html=True)

        if comptroller_url:
            st.caption(f"Source: [{comptroller_url}]({comptroller_url})")

        st.download_button(
            label="📥 Download Plat PDF",
            data=pdf_bytes,
            file_name="plat.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

        b64 = base64.b64encode(pdf_bytes).decode()
        st.markdown(f"""
        <div style="border:2px solid #1a365d; border-radius:10px; overflow:hidden; margin:1rem 0;">
            <iframe src="data:application/pdf;base64,{b64}"
                    width="100%" height="750px" style="border:none;"></iframe>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="step step-fail">❌ Could not retrieve plat PDF automatically</div>', unsafe_allow_html=True)
        if comptroller_url:
            st.markdown(f"**Try opening manually:** [{comptroller_url}]({comptroller_url})")

    # Debug always visible
    with st.expander("🔧 Debug log"):
        for line in debug_log:
            st.text(line)

elif go:
    st.warning("Enter an address.")
