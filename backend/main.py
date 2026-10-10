# University nursing faculty directory scraper and enrichment runner powered by LangGraph & Gemini AI
import os
import re
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

# Reconfigure standard output encoding to UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Load environment variables from local .env
load_dotenv()

# Import LangGraph workflow builder and exporter helpers
from src.workflow import create_scraper_graph
from src.nodes.exporter import export_contacts_to_excel

# Default university directory test URL
DEFAULT_URL = "https://www.asurams.edu/academic-affairs/dchealthprof/facultystaff-directory.php"

# Default extraction prompt
DEFAULT_PROMPT = "create an excel file of nursing faculty - include school, first name, last name, title, credentials, email, phone, and campus"

# Deduplicates contacts based on normalized school, first name, and last name
def deduplicate_contacts(contacts: list) -> list:
    seen = set()
    deduped = []
    for c in contacts:
        fn = str(c.get("first_name", "")).strip().lower()
        ln = str(c.get("last_name", "")).strip().lower()
        school = str(c.get("school", "")).strip().lower()
        key = f"{school}_{fn}_{ln}"
        if key not in seen and f"{fn}_{ln}" != "_":
            seen.add(key)
            deduped.append(c)
    return deduped

# Maximum simultaneous requests for scraping and Gemini AI extraction
MAX_CONCURRENT_REQUESTS = 5

# Processes a single URL for CLI execution
def process_cli_url(url: str, user_prompt: str, output_file: str, enrich_limit: int, compiled_graph):
    initial_state = {
        "url": url,
        "user_prompt": user_prompt,
        "output_file": output_file,
        "enrich_limit": enrich_limit
    }
    try:
        final_state = compiled_graph.invoke(initial_state)
        university = final_state.get("university", "").strip()

        if final_state.get("error"):
            err_msg = str(final_state["error"])
            return {"url": url, "contacts": [], "error": err_msg, "success": False}

        extracted = final_state.get("contacts", [])
        for c in extracted:
            if not c.get("school"):
                c["school"] = university or ""

        if len(extracted) == 0:
            return {"url": url, "contacts": [], "error": "No nursing contacts found on page", "success": False}
        else:
            return {
                "url": url,
                "contacts": extracted,
                "count": len(extracted),
                "status": final_state.get('status', f'Found {len(extracted)} contacts'),
                "success": True
            }
    except Exception as e:
        return {"url": url, "contacts": [], "error": str(e), "success": False}

# Runs the complete LangGraph pipeline across one or more directory URLs
def run_pipeline(urls: list, output_file: str, user_prompt: str, enrich_limit: int = None):
    enable_rr = os.getenv("ENABLE_ROCKETREACH", "true").strip().lower() in ["true", "1", "yes"]
    enable_hunter = os.getenv("ENABLE_HUNTER", "true").strip().lower() in ["true", "1", "yes"]

    print("\n" + "=" * 75)
    print("🤖 LangGraph & Gemini AI University Nursing Faculty Scraper")
    print("=" * 75)
    print(f"[*] Total URLs to process: {len(urls)}")
    print(f"[*] Extraction Prompt:     {user_prompt}")
    print(f"[*] Providers:            RocketReach [{'Enabled' if enable_rr else 'Disabled'}], Hunter.io [{'Enabled' if enable_hunter else 'Disabled'}]")
    print(f"[*] Target output file:   {output_file}")
    print("-" * 75)

    compiled_graph = create_scraper_graph()
    all_contacts = []
    failed_urls = []
    successful_urls = []
    effective_workers = min(MAX_CONCURRENT_REQUESTS, len(urls))

    print(f"[*] Running {len(urls)} directory URL(s) with {effective_workers} concurrent worker(s)...")

    # Process URLs concurrently with up to 5 workers
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_info = {
            executor.submit(process_cli_url, url, user_prompt, output_file, enrich_limit, compiled_graph): (idx, url)
            for idx, url in enumerate(urls, 1)
        }

        for future in as_completed(future_to_info):
            idx, url = future_to_info[future]
            try:
                res = future.result()
                if res.get("success"):
                    print(f"  ✓ [{idx}/{len(urls)}] Extracted {res.get('count', 0)} contact(s) from {url}")
                    all_contacts.extend(res.get("contacts", []))
                    successful_urls.append({"url": url, "count": res.get("count", 0)})
                else:
                    err = res.get("error", "Failed")
                    print(f"  ❌ [{idx}/{len(urls)}] Error processing {url}: {err}")
                    failed_urls.append({"url": url, "reason": err})
            except Exception as exc:
                print(f"  ❌ [{idx}/{len(urls)}] Workflow exception for {url}: {str(exc)}")
                failed_urls.append({"url": url, "reason": str(exc)})

    # Show failed URLs report if any
    if failed_urls:
        print("\n" + "!" * 75)
        print(f"⚠️  UNABLE TO EXTRACT CONTACTS FROM {len(failed_urls)} OF {len(urls)} URL(S):")
        for f in failed_urls:
            print(f"   • {f['url']} -> Reason: {f['reason']}")
        print("!" * 75)

    # Deduplicate accumulated contacts
    unique_contacts = deduplicate_contacts(all_contacts)
    print("\n" + "=" * 75)
    print(f"📊 Extraction Complete: {len(unique_contacts)} Unique Contacts Extracted")
    print("=" * 75)

    # Save consolidated contacts to styled Excel
    saved_path = export_contacts_to_excel(unique_contacts, output_file)
    print(f"\n🎉 Saved Excel file: {os.path.abspath(saved_path)}\n")

    # Render summary table in console with 8 columns
    print(f"{'#':<3} | {'School':<26} | {'First Name':<14} | {'Last Name':<14} | {'Title':<24} | {'Credentials':<16} | {'Email':<26} | {'Phone':<14} | {'Campus'}")
    print("-" * 155)
    for i, c in enumerate(unique_contacts[:15], 1):
        sch = str(c.get("school", ""))[:26]
        fn = str(c.get("first_name", ""))[:14]
        ln = str(c.get("last_name", ""))[:14]
        title = str(c.get("title", ""))[:24]
        creds = str(c.get("credentials") or c.get("credential") or "")[:16]
        email = str(c.get("email", ""))[:26]
        phone = str(c.get("phone", ""))[:14]
        camp = str(c.get("campus", ""))[:20]
        print(f"{i:<3} | {sch:<26} | {fn:<14} | {ln:<14} | {title:<24} | {creds:<16} | {email:<26} | {phone:<14} | {camp}")
    print("-" * 155 + "\n")

    return saved_path

# Main CLI entrypoint
def main():
    parser = argparse.ArgumentParser(description="LangGraph University Faculty Scraper & Excel Exporter")
    parser.add_argument("--urls", "-u", nargs="+", default=[], help="One or more directory URLs")
    parser.add_argument("--prompt", "-p", default=DEFAULT_PROMPT, help="Extraction instruction prompt")
    parser.add_argument("--output", "-o", default="", help="Output Excel filename")
    parser.add_argument("--enrich-limit", "-l", type=int, default=None, help="Enrichment search credit limit")
    args, _ = parser.parse_known_args()

    # Parse multiple URLs from CLI or prompt interactively
    if args.urls:
        urls = []
        for item in args.urls:
            for sub in item.split(','):
                sub = sub.strip()
                if sub:
                    urls.append(sub)
    elif not sys.stdin.isatty():
        urls = [DEFAULT_URL]
    else:
        print("\n" + "=" * 75)
        print("🎓 University Faculty Directory Scraper (LangGraph & Gemini AI)")
        print("=" * 75)
        print("\n[1] Enter website URL(s):")
        print("    (Enter multiple URLs separated by commas, or press Enter for default)")
        raw_in = input(f"    URL(s) [{DEFAULT_URL}]: ").strip()
        urls = [u.strip() for u in raw_in.split(",") if u.strip()] if raw_in else [DEFAULT_URL]

    # Determine prompt
    user_prompt = args.prompt

    # Determine output file name
    if args.output:
        out_file = args.output
    elif not sys.stdin.isatty():
        out_file = "nursing_faculty_langgraph.xlsx"
    else:
        raw_out = input("\n[2] Output Excel filename [nursing_faculty_langgraph.xlsx]: ").strip()
        out_file = raw_out or "nursing_faculty_langgraph.xlsx"

    if not out_file.endswith(".xlsx"):
        out_file += ".xlsx"

    run_pipeline(urls, out_file, user_prompt, enrich_limit=args.enrich_limit)

# Run entrypoint
if __name__ == "__main__":
    main()
