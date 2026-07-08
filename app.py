"""
PlatQuest 9000 — Orange County, FL plat retriever.

Give it a street address; it walks the same path a human would:

  1. Search the address on the Orange County Property Appraiser site
     (ocpaweb.ocpafl.org — an Angular SPA; everything renders client-side).
  2. Open the matching parcel page (navigate straight to the result link's
     href — normal clicks get intercepted by the fixed header overlay).
  3. Open the "Plats" section and take the link OCPA provides to the Orange
     County Comptroller's document viewer (selfservice.or.occompt.com).
     NOTE: we never construct Comptroller URLs from the Plat Book/Page in the
     legal description — that is a different numbering system from the OR
     Book/Page used in Comptroller URLs and yields wrong documents. We only
     follow the actual link the site gives us. We also explicitly ignore
     vab.occompt.com (Value Adjustment Board), which lurks in the footer.
  4. Load the viewer — an ~18 KB HTML shell whose JavaScript fetches the PDF —
     with a response listener registered BEFORE navigation, and capture the
     largest PDF response (thumbnails may come through too).

Deployment target: Streamlit Community Cloud (requirements.txt + packages.txt).
"""

import base64
import concurrent.futures
import re
import subprocess
import sys
import traceback
from datetime import datetime

import streamlit as st

st.set_page_config(page_title="PlatQuest 9000", page_icon="📜", layout="centered")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

OCPA_START_URLS = [
    "https://ocpaweb.ocpafl.org/",
    "https://ocpaweb.ocpafl.org/parcelsearch",
]

# Search-result links look like .../Parcel%20ID/<digits>. Filtering on this
# exact pattern keeps us off the site logo / nav links.
PARCEL_LINK_RE = re.compile(r"parcel(?:%20|%2520|\s)?id/\d+", re.IGNORECASE)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SEARCH_INPUT_SELECTORS = [
    "input[type='search']",
    "input[placeholder*='search' i]",
    "input[placeholder*='address' i]",
    "input[formcontrolname*='search' i]",
    "input[name*='search' i]",
    "input[aria-label*='search' i]",
    "input[type='text']",
    "input:not([type='hidden'])",
]


class WorkflowError(RuntimeError):
    """A failure we can explain to the user in plain language."""


# --------------------------------------------------------------------------- #
# One-time Chromium install (runs once per container)
# --------------------------------------------------------------------------- #

@st.cache_resource(show_spinner="Installing headless Chromium (first run only)…")
def ensure_chromium():
    """Download Playwright's Chromium. Cached so it runs once per container."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=900,
        )
        return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return False, f"playwright install failed: {exc}"


# --------------------------------------------------------------------------- #
# Page helpers
# --------------------------------------------------------------------------- #

def find_search_input(page, note):
    """Locate a visible search box on the current OCPA page."""
    for sel in SEARCH_INPUT_SELECTORS:
        try:
            for el in page.query_selector_all(sel):
                try:
                    if el.is_visible():
                        ph = el.get_attribute("placeholder") or ""
                        note(f'Search box found — selector: {sel}, placeholder: "{ph}"')
                        return el
                except Exception:
                    continue
        except Exception:
            continue
    note("No visible search input found on this page")
    return None


def dismiss_banners(page, note):
    """Best-effort dismissal of cookie/terms dialogs that block interaction."""
    pat = re.compile(r"^(accept|agree|i agree|ok|okay|got it|close|dismiss)$", re.IGNORECASE)
    try:
        loc = page.get_by_role("button", name=pat)
        for i in range(min(loc.count(), 3)):
            try:
                loc.nth(i).evaluate("e => e.click()")
                note("Dismissed a banner/dialog button")
                page.wait_for_timeout(600)
            except Exception:
                pass
    except Exception:
        pass


def plats_candidates(page, note):
    """Collect clickable elements that look like the 'Plats' tab, best first."""
    pat = re.compile(r"\bplats?\b", re.IGNORECASE)
    tiers = [
        ("role=tab", lambda: page.get_by_role("tab", name=pat)),
        ("role=button", lambda: page.get_by_role("button", name=pat)),
        ("role=link", lambda: page.get_by_role("link", name=pat)),
        ("exact text node", lambda: page.locator(
            "xpath=//*[normalize-space(text())='Plats' or normalize-space(text())='PLATS' or normalize-space(text())='Plat']"
        )),
        ("text contains", lambda: page.get_by_text(pat)),
    ]
    out = []
    for label, make in tiers:
        try:
            loc = make()
            n = min(loc.count(), 5)
            if n:
                note(f"Plats candidate(s) via {label}: {n}")
            for i in range(n):
                try:
                    handle = loc.nth(i).element_handle(timeout=2000)
                    if handle:
                        out.append(handle)
                except Exception:
                    continue
        except Exception:
            continue
        if len(out) >= 6:
            break
    return out[:6]


# --------------------------------------------------------------------------- #
# The workflow
# --------------------------------------------------------------------------- #

def find_plat(address: str) -> dict:
    """Run the full OCPA → Comptroller workflow for one address."""
    from playwright.sync_api import sync_playwright

    log: list = []
    shots: list = []
    result = {
        "pdf": None,
        "pdf_source_url": None,
        "comptroller_url": None,
        "parcel_url": None,
        "furthest_url": None,
        "error": None,
        "log": log,
        "screenshots": shots,
    }

    def note(msg: str):
        log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    note(f'Starting plat search for: "{address}"')

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1400, "height": 1000},
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.set_default_timeout(30_000)

            # ---- small helpers bound to this page --------------------------
            def snap(label: str):
                try:
                    shots.append((label, page.screenshot(full_page=True)))
                    note(f"Screenshot captured: {label}")
                except Exception as exc:
                    note(f"Screenshot failed ({label}): {exc}")

            def settle(extra_seconds: float = 4.5, label: str = ""):
                """networkidle if it comes, then a fixed wait for Angular to paint."""
                try:
                    page.wait_for_load_state("networkidle", timeout=20_000)
                except Exception:
                    note(f"networkidle not reached{' at ' + label if label else ''}; continuing after fixed wait")
                page.wait_for_timeout(int(extra_seconds * 1000))

            def goto(url: str, label: str, extra: float = 4.5):
                note(f"Navigating to: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                settle(extra, label)
                result["furthest_url"] = page.url
                note(f"Arrived at: {page.url}")

            def anchors():
                try:
                    return page.eval_on_selector_all(
                        "a[href]",
                        r"els => els.map(e => ({text: (e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120), href: e.href}))",
                    )
                except Exception as exc:
                    note(f"Anchor scan failed: {exc}")
                    return []

            def comptroller_links():
                """Links to the Comptroller viewer. selfservice/ssweb only; never vab.*"""
                out, seen = [], set()
                cur = (page.url or "")
                cl = cur.lower()
                if ("selfservice" in cl or "ssweb" in cl) and "vab" not in cl:
                    out.append({"text": "(current page)", "href": cur})
                    seen.add(cur)
                for a in anchors():
                    h = (a["href"] or "")
                    hl = h.lower()
                    if not h or h in seen:
                        continue
                    if "vab" in hl:  # Value Adjustment Board — wrong site, skip
                        continue
                    if "selfservice" in hl or "ssweb" in hl:
                        seen.add(h)
                        out.append(a)
                return out

            try:
                # ============ Step 1: search the address on OCPA ============
                search_box = None
                for start_url in OCPA_START_URLS:
                    goto(start_url, "OCPA landing")
                    dismiss_banners(page, note)
                    search_box = find_search_input(page, note)
                    if search_box:
                        break
                snap("OCPA search page")
                if not search_box:
                    raise WorkflowError("Could not find the search box on the OCPA site.")

                note("Typing the address into the search box")
                search_box.click()
                try:
                    search_box.fill("")
                except Exception:
                    pass
                search_box.type(address, delay=40)
                page.wait_for_timeout(800)
                search_box.press("Enter")
                settle(4.5, "search results")
                snap("Search results")

                # ============ Step 2: open the parcel page ==================
                if PARCEL_LINK_RE.search(page.url):
                    note("Search navigated straight to a parcel page")
                    parcel_href = page.url
                else:
                    matches, seen = [], set()
                    all_a = anchors()
                    for a in all_a:
                        h = a["href"] or ""
                        if PARCEL_LINK_RE.search(h) and h not in seen:
                            seen.add(h)
                            matches.append(a)
                    note(f"Parcel link(s) matching Parcel%20ID/<digits>: {len(matches)}")
                    for m in matches[:5]:
                        note(f'   • "{m["text"]}" → {m["href"]}')
                    if not matches:
                        note("No parcel links matched. Anchors on the page (for debugging):")
                        for a in all_a[:25]:
                            note(f'   – "{a["text"]}" → {a["href"]}')
                        raise WorkflowError(
                            "No parcel found for that address on OCPA. "
                            "Try a shorter form (street number + name, no city/ZIP)."
                        )
                    parcel_href = matches[0]["href"]

                # Navigate directly to the href — avoids the fixed-header
                # "element intercepts pointer events" click problem.
                if page.url != parcel_href:
                    goto(parcel_href, "parcel page")
                result["parcel_url"] = page.url
                snap("Parcel page")

                # ============ Step 3: Plats tab → Comptroller link ==========
                links = comptroller_links()
                if links:
                    note("Comptroller link already present in the DOM (no click needed)")
                    snap("Plats link located (no click needed)")
                else:
                    cands = plats_candidates(page, note)
                    if not cands:
                        snap("Parcel page (no Plats control found)")
                        raise WorkflowError(
                            "Could not find a 'Plats' tab on the parcel page — "
                            "the parcel may have no recorded plat, or the page layout changed."
                        )
                    for i, el in enumerate(cands, start=1):
                        try:
                            desc = el.evaluate(
                                r"e => e.tagName.toLowerCase() + ' \u201c' + (e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60) + '\u201d'"
                            )
                        except Exception:
                            desc = "<element>"
                        note(f"JS-clicking Plats candidate {i}/{len(cands)}: {desc}")
                        before = page.url
                        try:
                            el.evaluate("e => e.click()")  # JS click beats overlay interception
                        except Exception as exc:
                            note(f"   click threw: {exc}")
                            continue
                        page.wait_for_timeout(2500)
                        cur = page.url.lower()
                        went_to_comptroller = ("selfservice" in cur or "ssweb" in cur) and "vab" not in cur
                        if page.url != before and "ocpaweb.ocpafl.org" not in cur and not went_to_comptroller:
                            note(f"   click navigated off-site to {page.url}; going back")
                            try:
                                page.go_back(wait_until="domcontentloaded")
                                page.wait_for_timeout(1500)
                            except Exception:
                                pass
                            continue
                        links = comptroller_links()
                        if links:
                            break
                    snap("After clicking Plats")
                    if not links:
                        note("No Comptroller link yet — waiting 3 more seconds and rescanning")
                        page.wait_for_timeout(3000)
                        links = comptroller_links()

                if not links:
                    note("Anchors on the parcel page (for debugging):")
                    for a in anchors()[:30]:
                        note(f'   – "{a["text"]}" → {a["href"]}')
                    raise WorkflowError(
                        "Found the parcel, but no plat link appeared under the Plats tab. "
                        "The property may not have a recorded plat."
                    )

                for a in links:
                    note(f'Comptroller link: "{a["text"]}" → {a["href"]}')
                chosen = next(
                    (a for a in links if "continue" in (a["text"] or "").lower()),
                    links[0],
                )
                comptroller_url = chosen["href"]
                result["comptroller_url"] = comptroller_url
                result["furthest_url"] = comptroller_url
                note(f"Following the Comptroller link OCPA provided: {comptroller_url}")

                # ============ Step 4: capture the PDF =======================
                # The document URL returns an HTML shell; its JS then fetches
                # the PDF. Register the listener BEFORE navigating.
                captured: list = []
                page.on("response", lambda r: captured.append(r))

                goto(comptroller_url, "Comptroller viewer", extra=5.0)
                snap("Comptroller viewer")

                processed = set()
                pdfs: list = []

                def harvest():
                    for r in list(captured):
                        if id(r) in processed:
                            continue
                        processed.add(id(r))
                        url = r.url
                        try:
                            ctype = (r.headers.get("content-type") or "").lower()
                        except Exception:
                            ctype = ""
                        # Only bother reading bodies that could plausibly be the PDF
                        if "pdf" not in ctype and "occompt" not in url.lower():
                            continue
                        try:
                            body = r.body()
                        except Exception as exc:
                            note(f"   (could not read body of {url[:100]}: {exc})")
                            continue
                        if body.startswith(b"%PDF") or "pdf" in ctype:
                            try:
                                status = r.status
                            except Exception:
                                status = "?"
                            pdfs.append((len(body), body, url))
                            note(
                                f"Captured PDF candidate: {url[:120]} — "
                                f"{len(body):,} bytes (HTTP {status}, content-type: {ctype or 'n/a'})"
                            )

                harvest()
                if not pdfs:
                    note("No PDF captured yet — giving the viewer JS 6 more seconds")
                    page.wait_for_timeout(6000)
                    harvest()
                    snap("Comptroller viewer (after extra wait)")

                if not pdfs:
                    note("Responses observed on the Comptroller page (for debugging):")
                    for r in list(captured)[:30]:
                        try:
                            ctype = (r.headers.get("content-type") or "")[:40]
                        except Exception:
                            ctype = "?"
                        note(f"   – HTTP {getattr(r, 'status', '?')} [{ctype}] {r.url[:110]}")
                    raise WorkflowError(
                        "Reached the Comptroller viewer, but no PDF response was captured. "
                        "Open the link below to view it manually."
                    )

                pdfs.sort(key=lambda t: t[0], reverse=True)
                size, body, src = pdfs[0]
                note(f"Selected largest PDF: {size:,} bytes from {src[:120]}")
                result["pdf"] = body
                result["pdf_source_url"] = src

            except WorkflowError as exc:
                result["error"] = str(exc)
                note(f"FAILED: {exc}")
                snap("State at failure")
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"Unexpected error: {exc}"
                note("FAILED (unexpected):\n" + traceback.format_exc())
                snap("State at failure")
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    except Exception as exc:  # Playwright itself failed to start
        result["error"] = f"Could not start the headless browser: {exc}"
        note("FAILED before browser start:\n" + traceback.format_exc())

    return result


def run_workflow(address: str) -> dict:
    """Run find_plat in a dedicated thread so Playwright's sync API never
    collides with any asyncio loop on Streamlit's script thread."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(find_plat, address).result()


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

CHROMIUM_READY, CHROMIUM_LOG = ensure_chromium()

st.title("📜 PlatQuest 9000")
st.caption(
    "Enter an Orange County, Florida street address to retrieve its recorded plat PDF — "
    "pulled live from the Property Appraiser and Comptroller sites."
)

if not CHROMIUM_READY:
    st.error("Chromium could not be installed, so plat retrieval is unavailable.")
    with st.expander("Playwright install log"):
        st.code(CHROMIUM_LOG or "(empty)", language=None)

with st.form("plat_form"):
    address = st.text_input("Street address", placeholder="763 Golden Sunshine Cir")
    submitted = st.form_submit_button(
        "🔎 Find Plat", type="primary", use_container_width=True, disabled=not CHROMIUM_READY
    )

if submitted:
    addr = (address or "").strip()
    if not addr:
        st.warning("Enter a street address first.")
        st.stop()

    with st.spinner("Walking OCPA → Comptroller for your plat — takes about 30 seconds…"):
        res = run_workflow(addr)

    if res["pdf"]:
        doc_id = (res.get("comptroller_url") or "").rstrip("/").split("/")[-1]
        doc_id = re.sub(r"[^A-Za-z0-9_-]+", "_", doc_id) or re.sub(r"[^A-Za-z0-9_-]+", "_", addr)
        source_md = f" · [source document]({res['comptroller_url']})" if res.get("comptroller_url") else ""
        st.success(f"Plat found — {len(res['pdf']):,} bytes{source_md}")

        st.download_button(
            "⬇️ Download plat PDF",
            data=res["pdf"],
            file_name=f"plat_{doc_id}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )
        b64 = base64.b64encode(res["pdf"]).decode()
        st.markdown(
            f"<iframe src='data:application/pdf;base64,{b64}' width='100%' height='750' "
            f"style='border:1px solid #d9d9d9; border-radius:10px;'></iframe>",
            unsafe_allow_html=True,
        )
    else:
        st.error(res.get("error") or "Something went wrong retrieving the plat.")
        if res.get("furthest_url"):
            st.markdown(
                f"**Continue manually from where the app stopped:** "
                f"[{res['furthest_url']}]({res['furthest_url']})"
            )

    with st.expander("🪵 Debug log", expanded=not bool(res["pdf"])):
        st.code("\n".join(res["log"]) or "(empty)", language=None)

    with st.expander("📸 Step-by-step screenshots"):
        if res["screenshots"]:
            for label, png in res["screenshots"]:
                st.image(png, caption=label, use_container_width=True)
        else:
            st.write("No screenshots were captured.")
