# LangGraph node for contact email enrichment via Hunter.io without limits
import os
import re
import sys
import requests
from dotenv import load_dotenv
from src.state import ScraperState

# Load environment variables
load_dotenv()

# Reconfigure stdout encoding to UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Searches Hunter.io Email Finder by person name and institutional domain
def search_hunter_email(first_name: str, last_name: str, domain: str, api_key: str) -> dict:
    try:
        url = "https://api.hunter.io/v2/email-finder"
        # Clean domain to root domain for accurate Hunter matching
        clean_domain = domain.replace("www.", "").strip().lower()
        parts = clean_domain.split(".")
        if len(parts) >= 2:
            clean_domain = ".".join(parts[-2:])
        params = {"first_name": first_name, "last_name": last_name, "domain": clean_domain, "api_key": api_key}
        res = requests.get(url, params=params, timeout=8)
        if res.status_code == 200:
            return res.json().get("data", {})
    except Exception:
        pass
    return {}

# LangGraph node function executing Hunter.io email enrichment for all missing emails
def enrich_node(state: ScraperState) -> ScraperState:
    # Abort node if an error occurred in previous nodes
    if state.get("error"):
        return state

    contacts = state.get("contacts", [])
    domain = state.get("domain", "")

    enable_hunter = os.getenv("ENABLE_HUNTER", "true").strip().lower() in ["true", "1", "yes"]
    hunter_key = os.getenv("HUNTER_API_KEY", "").strip()

    # Skip if Hunter enrichment service is disabled or key missing
    if not enable_hunter or not hunter_key:
        return {"status": "Enrichment skipped: Hunter.io disabled or API key missing"}

    # Find contact indices missing email
    missing_indices = [i for i, c in enumerate(contacts) if not str(c.get("email") or "").strip()]
    if not missing_indices:
        return {"status": "All contacts already contain email information"}

    print(f"\n[*] Enriching all {len(missing_indices)} contact(s) missing emails via Hunter.io...")

    enriched_count = 0
    for idx in missing_indices:
        c = contacts[idx]
        fn = str(c.get("first_name") or "").strip()
        ln = str(c.get("last_name") or "").strip()
        full_name = f"{fn} {ln}".strip()
        print(f"  -> Querying Hunter.io for: {full_name} ({domain})...")

        # Search Hunter.io for email
        if fn and ln:
            h_data = search_hunter_email(fn, ln, domain, hunter_key)
            if h_data and h_data.get("email"):
                found_email = h_data["email"].strip()
                c["email"] = found_email
                print(f"     [+] Found email via Hunter.io: {found_email}")
                enriched_count += 1
            else:
                print(f"     [-] No email match found in Hunter.io for {full_name}")

    return {
        "contacts": contacts,
        "status": f"Enrichment completed: updated {enriched_count} email(s) via Hunter.io"
    }
