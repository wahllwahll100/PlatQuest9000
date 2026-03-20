"""
PlatQuest 9000 — Orange County FL Plat Lookup
Enter an address → get the development plat PDF. That's it.

Flow:
  1. Playwright opens OCPA, searches the address, gets to the parcel page
  2. Playwright reads the plat/deed book & page from the parcel page
  3. Playwright opens the Comptroller document page and captures the PDF
  4. PDF displayed inline with download button
"""

import streamlit as st
import subprocess
import re
import base64
import json

st.set_page_config(page_title="PlatQuest 9000", page_icon="📄", layout="wide")

# ── Styling ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

.stApp { font-family: 'Space Grotesk', sans-serif; }

.app-header {
    background: linear-gradient(135deg, #0c1b2a 0%, #1a365d 100%);
    color: white; padding: 2rem; border-radius: 12px; margin-bottom: 1.5rem;
    text-align: center;
}
.app-header h1 { font-size: 2.2rem; font-weight: 700; margin: 0; }
.app-header p { color: #94a3b8; margin: 0.5rem 0 0; font-size: 1rem; }

.step-card {
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 12px 16px; margin: 6px 0; font-size: 13px;
}
.step-card.ok { border-left: 4px solid #22c55e; }
.step-card.fail { border-left: 4px solid #ef4444; }
.step-card.working { border-left: 4px solid #3b82f6; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="app-header">
    <h1>📄 PlatQuest 9000</h1>
    <p>Orange County, FL — Address → Plat PDF</p>
</div>
""", unsafe_allow_html=True)


# ── Playwright setup ─────────────────────────────────────────────────────────

@st.cache_resource
def install_playwright():
    """Install Chromium once, cached across sessions."""
    try:
        result = subprocess.run(
            ["playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=120
        )
        return True, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def search_ocpa_and_get_plat_ref(address):
    """
    Step 1+2: Use Playwright to:
      - Search OCPA for the address
      - Navigate to the parcel page
      - Find the OR Book/Page (Deed Book/Page) for the plat

    Returns dict with: parcel_id, or_book, or_page, ocpa_url, debug
    """
    from playwright.sync_api import sync_playwright

    result = {
        "parcel_id": None, "or_book": None, "or_page": None,
        "ocpa_url": None, "debug": []
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ))

            # ── Navigate to OCPA search by site address ──
            search_url = f"https://ocpaweb.ocpafl.org/parcelsearch/Site%20Address/{address.upper().replace(' ', '%20')}"
            result["debug"].append(f"Opening: {search_url}")
            page.goto(search_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            current_url = page.url
            result["debug"].append(f"Landed on: {current_url}")

            # Check if we landed on a parcel page or a search results list
            page_text = page.inner_text("body")

            # Try to extract parcel ID from the URL or page
            pid_match = re.search(r'/Parcel%20ID/(\d+)', current_url)
            if pid_match:
                result["parcel_id"] = pid_match.group(1)
                result["ocpa_url"] = current_url
                result["debug"].append(f"Direct hit — Parcel ID: {result['parcel_id']}")
            else:
                # Might be a search results page — look for parcel links
                result["debug"].append("Search results page — looking for parcel links...")
                links = page.query_selector_all("a[href*='Parcel']")
                for link in links[:5]:
                    href = link.get_attribute("href") or ""
                    pm = re.search(r'Parcel%20ID/(\d+)', href)
                    if pm:
                        result["parcel_id"] = pm.group(1)
                        # Navigate to that parcel
                        full_url = href if href.startswith("http") else f"https://ocpaweb.ocpafl.org{href}"
                        result["ocpa_url"] = full_url
                        result["debug"].append(f"Found parcel link: {result['parcel_id']}")
                        page.goto(full_url, wait_until="networkidle", timeout=30000)
                        page.wait_for_timeout(3000)
                        break

                # If still no parcel, try clicking the first result row
                if not result["parcel_id"]:
                    try:
                        rows = page.query_selector_all("tr[class*='cursor'], div[class*='result'], a[class*='parcel']")
                        if rows:
                            rows[0].click()
                            page.wait_for_load_state("networkidle", timeout=15000)
                            page.wait_for_timeout(2000)
                            current_url = page.url
                            pid_match = re.search(r'/Parcel%20ID/(\d+)', current_url)
                            if pid_match:
                                result["parcel_id"] = pid_match.group(1)
                                result["ocpa_url"] = current_url
                                result["debug"].append(f"Clicked first result → Parcel: {result['parcel_id']}")
                    except Exception as click_err:
                        result["debug"].append(f"Click attempt: {click_err}")

            if not result["parcel_id"]:
                # Last resort: search page text for a 15-digit parcel ID
                pid_in_text = re.search(r'\b(\d{15})\b', page_text)
                if pid_in_text:
                    result["parcel_id"] = pid_in_text.group(1)
                    result["ocpa_url"] = f"https://ocpaweb.ocpafl.org/parcelsearch/Parcel%20ID/{result['parcel_id']}"
                    result["debug"].append(f"Found PID in page text: {result['parcel_id']}")
                    page.goto(result["ocpa_url"], wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(3000)

            if not result["parcel_id"]:
                result["debug"].append("❌ Could not find parcel ID")
                result["debug"].append(f"Page text (500 chars): {page_text[:500]}")
                browser.close()
                return result

            # ── Now on the parcel page — find OR Book/Page ──
            result["debug"].append("Searching for Deed/OR Book & Page on parcel page...")

            # The OCPA page is JavaScript-rendered. Get full rendered HTML.
            html = page.content()
            full_text = page.inner_text("body")

            # Try clicking a "Plat" or "Legal" or "Location" tab if one exists
            for tab_text in ["Plat", "Legal", "Location", "Recording", "Deed", "Sales"]:
                try:
                    tab = page.get_by_text(tab_text, exact=False).first
                    if tab and tab.is_visible():
                        tab.click()
                        page.wait_for_timeout(2000)
                        html = page.content()
                        full_text = page.inner_text("body")
                        result["debug"].append(f"Clicked tab: {tab_text}")
                        break
                except Exception:
                    pass

            # Extract OR Book/Page from rendered content
            # Patterns for Deed Book / Deed Page (the OR reference)
            for source_text in [full_text, html]:
                if result["or_book"]:
                    break
                for bk_pat in [
                    r'Deed\s*Book[:\s]*(\d{3,})',
                    r'OR\s*Book[:\s]*(\d{3,})',
                    r'Recording\s*Book[:\s]*(\d{3,})',
                    r'Official\s*Record.*?Book[:\s]*(\d{3,})',
                    r'Book[:\s/]*(\d{4,})',  # 4+ digits likely OR Book
                ]:
                    m = re.search(bk_pat, source_text, re.IGNORECASE)
                    if m:
                        result["or_book"] = m.group(1)
                        result["debug"].append(f"Found OR Book: {result['or_book']}")
                        break

                for pg_pat in [
                    r'Deed\s*Page[:\s]*(\d{3,})',
                    r'OR\s*Page[:\s]*(\d{3,})',
                    r'Recording\s*Page[:\s]*(\d{3,})',
                    r'Official\s*Record.*?Page[:\s]*(\d{3,})',
                ]:
                    m = re.search(pg_pat, source_text, re.IGNORECASE)
                    if m:
                        result["or_page"] = m.group(1)
                        result["debug"].append(f"Found OR Page: {result['or_page']}")
                        break

            # If still missing, try element-by-element scan
            if not result["or_book"]:
                result["debug"].append("Scanning page elements for Deed Book/Page...")
                elements = page.query_selector_all("td, span, div, dd, li, p")
                for i, el in enumerate(elements[:300]):
                    try:
                        txt = el.inner_text().strip()
                        if re.search(r'Deed\s*Book|OR\s*Book|Plat\s*Book', txt, re.IGNORECASE) and not result["or_book"]:
                            nums = re.findall(r'(\d{3,})', txt)
                            if nums:
                                result["or_book"] = nums[-1]  # last number in the element
                                result["debug"].append(f"Element scan Book: '{txt[:80]}' → {result['or_book']}")
                            elif i + 1 < len(elements):
                                next_txt = elements[i+1].inner_text().strip()
                                nums = re.findall(r'(\d{3,})', next_txt)
                                if nums:
                                    result["or_book"] = nums[0]
                                    result["debug"].append(f"Next element Book: '{next_txt[:80]}' → {result['or_book']}")
                        elif re.search(r'Deed\s*Page|OR\s*Page|Plat\s*Page', txt, re.IGNORECASE) and not result["or_page"]:
                            nums = re.findall(r'(\d{3,})', txt)
                            if nums:
                                result["or_page"] = nums[-1]
                                result["debug"].append(f"Element scan Page: '{txt[:80]}' → {result['or_page']}")
                            elif i + 1 < len(elements):
                                next_txt = elements[i+1].inner_text().strip()
                                nums = re.findall(r'(\d{3,})', next_txt)
                                if nums:
                                    result["or_page"] = nums[0]
                                    result["debug"].append(f"Next element Page: '{next_txt[:80]}' → {result['or_page']}")
                    except Exception:
                        pass

            if not result["or_book"]:
                result["debug"].append("❌ Could not find OR Book/Page on parcel page")
                # Dump text for debugging
                result["debug"].append(f"Full page text (1000 chars):\n{full_text[:1000]}")

            browser.close()

    except Exception as e:
        result["debug"].append(f"Playwright error: {e}")

    return result


def fetch_comptroller_pdf(doc_ref):
    """
    Step 3: Use Playwright to open the Comptroller document page and
    capture the PDF from network traffic.

    Returns: (pdf_bytes, debug_info)
    """
    from playwright.sync_api import sync_playwright

    url = f"https://selfservice.or.occompt.com/ssweb/web/integration/document/{doc_ref}"
    pdf_bytes = None
    debug = [f"Opening: {url}"]

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )
            page = context.new_page()

            captured = []

            def on_response(response):
                try:
                    ct = response.headers.get("content-type", "").lower()
                    if "pdf" in ct or response.url.lower().endswith(".pdf"):
                        body = response.body()
                        if body and body[:4] == b'%PDF':
                            captured.append(body)
                except Exception:
                    pass

            page.on("response", on_response)

            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(5000)  # let PDF viewer JS finish

            if captured:
                pdf_bytes = max(captured, key=len)
                debug.append(f"✅ Captured PDF from network ({len(pdf_bytes):,} bytes)")
            else:
                debug.append("No PDF in network traffic")

                # Try finding iframe/embed with PDF
                for sel in ["iframe", "embed", "object"]:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            src = el.get_attribute("src") or el.get_attribute("data") or ""
                            if src:
                                debug.append(f"Found <{sel}> src: {src[:100]}")
                                if not src.startswith("http"):
                                    src = f"https://selfservice.or.occompt.com{src}"
                                resp = context.request.get(src)
                                body = resp.body()
                                if body[:4] == b'%PDF':
                                    pdf_bytes = body
                                    debug.append(f"✅ Got PDF from <{sel}> ({len(pdf_bytes):,} bytes)")
                                    break
                    except Exception:
                        pass

                # Try download buttons
                if not pdf_bytes:
                    for btn_text in ["Download", "View", "PDF", "Print"]:
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
                                    debug.append(f"✅ Got PDF from button click ({len(pdf_bytes):,} bytes)")
                                else:
                                    pdf_bytes = None
                                break
                        except Exception:
                            pass

                if not pdf_bytes:
                    page_html = page.content()[:2000]
                    debug.append(f"Page HTML (2000 chars):\n{page_html}")

            browser.close()

    except Exception as e:
        debug.append(f"Error: {e}")

    return pdf_bytes, debug


# ── Main UI ──────────────────────────────────────────────────────────────────

# API key from secrets or manual
try:
    anthropic_key = st.secrets.get("ANTHROPIC_API_KEY", "")
except Exception:
    anthropic_key = ""

address = st.text_input(
    "🏠 Enter an Orange County address:",
    placeholder="763 Golden Sunshine Cir",
)

col1, col2 = st.columns([1, 3])
with col1:
    go = st.button("🔍 Find Plat", use_container_width=True, type="primary")
with col2:
    st.caption("Searches OCPA → finds parcel → fetches plat PDF from Comptroller")

if go and address:
    # Install Playwright browsers (cached)
    with st.spinner("⚙️ Preparing browser engine..."):
        pw_ok, pw_msg = install_playwright()

    if not pw_ok:
        st.error(f"Failed to install browser: {pw_msg}")
        st.stop()

    # ── STEP 1+2: Search OCPA and get OR Book/Page ──
    step1 = st.empty()
    step1.markdown('<div class="step-card working">🔍 <strong>Step 1:</strong> Searching OCPA for address...</div>', unsafe_allow_html=True)

    ocpa_result = search_ocpa_and_get_plat_ref(address)

    if ocpa_result["parcel_id"]:
        step1.markdown(
            f'<div class="step-card ok">✅ <strong>Step 1:</strong> Found parcel <code>{ocpa_result["parcel_id"]}</code></div>',
            unsafe_allow_html=True
        )
    else:
        step1.markdown(
            '<div class="step-card fail">❌ <strong>Step 1:</strong> Could not find parcel</div>',
            unsafe_allow_html=True
        )
        with st.expander("🔧 Debug log"):
            for d in ocpa_result["debug"]:
                st.text(d)
        st.stop()

    # Show OR Book/Page status
    step2 = st.empty()
    if ocpa_result["or_book"] and ocpa_result["or_page"]:
        doc_ref = f"{ocpa_result['or_book']}P{ocpa_result['or_page'].zfill(6)}"
        step2.markdown(
            f'<div class="step-card ok">✅ <strong>Step 2:</strong> OR Book <strong>{ocpa_result["or_book"]}</strong> / Page <strong>{ocpa_result["or_page"]}</strong> → <code>{doc_ref}</code></div>',
            unsafe_allow_html=True
        )
    else:
        step2.markdown(
            '<div class="step-card fail">⚠️ <strong>Step 2:</strong> Could not auto-detect OR Book/Page from OCPA</div>',
            unsafe_allow_html=True
        )
        # Manual fallback
        st.markdown(f"Open the OCPA page and find **Deed Book** and **Deed Page**: [OCPA Property Card]({ocpa_result['ocpa_url']})")
        mc1, mc2 = st.columns(2)
        with mc1:
            m_book = st.text_input("Deed/OR Book:", placeholder="1985", key="m_book")
        with mc2:
            m_page = st.text_input("Deed/OR Page:", placeholder="15044", key="m_page")
        if m_book and m_page:
            ocpa_result["or_book"] = m_book.strip()
            ocpa_result["or_page"] = m_page.strip()
            doc_ref = f"{ocpa_result['or_book']}P{ocpa_result['or_page'].zfill(6)}"
        else:
            with st.expander("🔧 Debug log"):
                for d in ocpa_result["debug"]:
                    st.text(d)
            st.stop()

    # ── STEP 3: Fetch the PDF ──
    comptroller_url = f"https://selfservice.or.occompt.com/ssweb/web/integration/document/{doc_ref}"

    step3 = st.empty()
    step3.markdown(
        f'<div class="step-card working">📥 <strong>Step 3:</strong> Fetching PDF from Comptroller... <code>{doc_ref}</code></div>',
        unsafe_allow_html=True
    )

    pdf_bytes, pdf_debug = fetch_comptroller_pdf(doc_ref)

    if pdf_bytes:
        step3.markdown(
            f'<div class="step-card ok">✅ <strong>Step 3:</strong> PDF retrieved — <strong>{len(pdf_bytes):,} bytes</strong></div>',
            unsafe_allow_html=True
        )

        # Download button
        st.download_button(
            label=f"📥 Download Plat PDF — {doc_ref}.pdf",
            data=pdf_bytes,
            file_name=f"Plat_{doc_ref}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

        # Inline viewer
        b64 = base64.b64encode(pdf_bytes).decode()
        st.markdown(f"""
        <div style="border:2px solid #1a365d; border-radius:10px; overflow:hidden; margin:1rem 0;">
            <iframe src="data:application/pdf;base64,{b64}"
                    width="100%" height="750px" style="border:none;"></iframe>
        </div>
        """, unsafe_allow_html=True)
    else:
        step3.markdown(
            f'<div class="step-card fail">⚠️ <strong>Step 3:</strong> Could not auto-capture PDF</div>',
            unsafe_allow_html=True
        )
        st.markdown(f"**Open manually:** [{doc_ref}]({comptroller_url})")

    # Always show debug
    with st.expander("🔧 Full debug log"):
        st.markdown("**OCPA search:**")
        for d in ocpa_result["debug"]:
            st.text(d)
        st.markdown("---")
        st.markdown("**PDF fetch:**")
        for d in pdf_debug:
            st.text(d)
        st.markdown("---")
        st.markdown(f"**Comptroller URL:** `{comptroller_url}`")
        if ocpa_result.get("ocpa_url"):
            st.markdown(f"**OCPA URL:** `{ocpa_result['ocpa_url']}`")

elif go:
    st.warning("Enter an address.")
