# PlatQuest 9000

Streamlit app: enter an Orange County, FL street address → get the recorded plat PDF.

It drives headless Chromium (Playwright) through the same steps a human takes:
OCPA parcel search → parcel page → Plats tab → the "Continue to site" link →
Orange County Comptroller document viewer → capture the PDF from network responses.

## Deploy (Streamlit Community Cloud)

1. Push `app.py`, `requirements.txt`, and `packages.txt` to a GitHub repo.
2. New app → point it at `app.py` → deploy.
   The first boot downloads Chromium (~1 min); it's cached afterwards
   (`playwright install chromium` runs once per container via `@st.cache_resource`).

## Run locally

```
pip install -r requirements.txt
streamlit run app.py
```

Chromium installs itself on first launch. Every run shows a debug log and
step-by-step screenshots in expanders; on failure, the furthest URL reached
is shown as a clickable link so you can continue manually.

## Test case

Input `763 Golden Sunshine Cir` — should retrieve the plat at
`https://selfservice.or.occompt.com/ssweb/web/integration/document/1985P015044`.
