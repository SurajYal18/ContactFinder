# LangGraph node for contact email enrichment via Hunter.io and RocketReach (RocketSearch)
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

# Searches RocketReach / RocketSearch by person name and institutional employer or domain
def search_rocketreach_email(full_name: str, employer: str, domain: str, api_key: str) -> dict:
    try:
        url = "https://api.rocketreach.co/api/v2/person/lookup"
        headers = {
            "Api-Key": api_key,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        params = {"name": full_name.strip()}
        if employer and employer.strip():
            params["current_employer"] = employer.strip()
        elif domain and domain.strip():
            params["current_employer"] = domain.strip()

        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            emails = data.get("emails", [])
            clean_domain = domain.replace("www.", "").strip().lower()
            best_email = None
            for e in emails:
                addr = str(e.get("email") or "").strip()
                if not addr:
                    continue
                if clean_domain and clean_domain in addr.lower():
                    best_email = addr
                    break
                if e.get("type") == "professional" and not best_email:
                    best_email = addr
                elif not best_email:
                    best_email = addr
            return {
                "email": best_email or (emails[0].get("email") if emails else ""),
                "raw": data
            }
    except Exception:
        pass
    return {}

# LangGraph node function executing email enrichment for missing emails
def enrich_node(state: ScraperState) -> ScraperState:
    # Abort node if an error occurred in previous nodes
    if state.get("error"):
        return state

    contacts = state.get("contacts", [])
    domain = state.get("domain", "")
    university = state.get("university", "")
    provider_choice = str(state.get("enrich_provider") or "hunter").strip().lower()

    # If provider is explicitly disabled by user request
    if provider_choice in ["none", "disabled", "off"]:
        return {"status": "Enrichment skipped: Provider set to none"}

    # Read environment variables and feature flags
    load_dotenv(override=True)
    enable_hunter = os.getenv("ENABLE_HUNTER", "true").strip().lower() in ["true", "1", "yes"]
    hunter_key = os.getenv("HUNTER_API_KEY", "").strip()

    enable_rocketreach = (
        os.getenv("ENABLE_ROCKETREACH", "true").strip().lower() in ["true", "1", "yes"] and
        os.getenv("ENABLE_ROCKETSEARCH", "true").strip().lower() in ["true", "1", "yes"]
    )
    rocketreach_key = os.getenv("ROCKETREACH_API_KEY", "").strip() or os.getenv("ROCKETSEARCH_API_KEY", "").strip()

    # Determine effective providers based on user choice and environment enablement
    use_hunter = False
    use_rocketreach = False

    if provider_choice in ["hunter", "both"]:
        if enable_hunter and hunter_key:
            use_hunter = True
        else:
            print("[*] Hunter.io requested but disabled via env (ENABLE_HUNTER=false) or API key missing.")

    if provider_choice in ["rocketreach", "rocketsearch", "both"]:
        if enable_rocketreach and rocketreach_key:
            use_rocketreach = True
        else:
            print("[*] RocketSearch/RocketReach requested but disabled via env (ENABLE_ROCKETREACH=false) or API key missing.")

    if not use_hunter and not use_rocketreach:
        return {"status": f"Enrichment skipped: Requested provider ({provider_choice}) is disabled in env or missing API keys."}

    # Find contact indices missing email
    missing_indices = [i for i, c in enumerate(contacts) if not str(c.get("email") or "").strip()]
    if not missing_indices:
        return {"status": "All contacts already contain email information"}

    provider_label = "Hunter.io & RocketReach" if (use_hunter and use_rocketreach) else ("Hunter.io" if use_hunter else "RocketReach")
    print(f"\n[*] Enriching {len(missing_indices)} contact(s) missing emails via {provider_label}...")

    enriched_count = 0
    for idx in missing_indices:
        c = contacts[idx]
        fn = str(c.get("first_name") or "").strip()
        ln = str(c.get("last_name") or "").strip()
        full_name = f"{fn} {ln}".strip()
        found_email = ""
        source = ""

        # 1. Try Hunter.io if enabled
        if use_hunter and fn and ln:
            print(f"  -> Querying Hunter.io for: {full_name} ({domain})...")
            h_data = search_hunter_email(fn, ln, domain, hunter_key)
            if h_data and h_data.get("email"):
                found_email = h_data["email"].strip()
                source = "Hunter.io"

        # 2. Try RocketReach if enabled and no email found yet
        if not found_email and use_rocketreach and full_name:
            print(f"  -> Querying RocketReach for: {full_name} ({university or domain})...")
            rr_data = search_rocketreach_email(full_name, university, domain, rocketreach_key)
            if rr_data and rr_data.get("email"):
                found_email = rr_data["email"].strip()
                source = "RocketReach"

        if found_email:
            c["email"] = found_email
            c["email_source"] = source
            c["enrichment_status"] = f"Enriched via {source}"
            print(f"     [+] Found email via {source}: {found_email}")
            enriched_count += 1
        else:
            print(f"     [-] No email match found for {full_name}")

    return {
        "contacts": contacts,
        "status": f"Enrichment completed: updated {enriched_count} email(s) via {provider_label}"
    }
