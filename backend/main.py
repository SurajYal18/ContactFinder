# University nursing faculty directory scraper and enrichment runner powered by LangGraph & Gemini AI
import os
import re
import sys
import argparse
from dotenv import load_dotenv

# Reconfigure standard output encoding to UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Load environment variables from local .env
load_dotenv()

# Import LangGraph workflow builder and exporter helpers
from src.workflow import create_scraper_graph
from src.nodes.exporter import export_contacts_to_excel

# Default university directory test URL
DEFAULT_URL = "https://www.asurams.edu/academic-affairs/dchealthprof/facultystaff-directory.php"

# Default extraction prompt
DEFAULT_PROMPT = "create an excel file of nursing faculty - include first name, last name, credentials, title, campus, email and phone"

# Deduplicates contacts based on normalized first and last name
def deduplicate_contacts(contacts: list) -> list:
    seen = set()
    deduped = []
    for c in contacts:
        fn = str(c.get("first_name", "")).strip().lower()
        ln = str(c.get("last_name", "")).strip().lower()
        key = f"{fn}_{ln}"
        if key not in seen and key != "_":
            seen.add(key)
            deduped.append(c)
    return deduped

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

    # Process each directory URL individually to isolate failures
    for idx, url in enumerate(urls, 1):
        print(f"\n🌐 [{idx}/{len(urls)}] Processing URL: {url}")
        print("-" * 75)

        initial_state = {
            "url": url,
            "user_prompt": user_prompt,
            "output_file": output_file,
            "enrich_limit": enrich_limit
        }

        try:
            # Execute LangGraph StateGraph pipeline
            final_state = compiled_graph.invoke(initial_state)

            if final_state.get("error"):
                print(f"  ❌ Error processing {url}: {final_state['error']}")
                continue

            extracted = final_state.get("contacts", [])
            print(f"  ✓ {final_state.get('status', 'Completed')}")
            all_contacts.extend(extracted)
        except Exception as e:
            print(f"  ❌ Workflow exception for {url}: {str(e)}")
            continue

    # Deduplicate accumulated contacts
    unique_contacts = deduplicate_contacts(all_contacts)
    print("\n" + "=" * 75)
    print(f"📊 Extraction Complete: {len(unique_contacts)} Unique Contacts Extracted")
    print("=" * 75)

    # Save consolidated contacts to styled Excel
    saved_path = export_contacts_to_excel(unique_contacts, output_file)
    print(f"\n🎉 Saved Excel file: {os.path.abspath(saved_path)}\n")

    # Render summary table in console
    print(f"{'#':<3} | {'First Name':<14} | {'Last Name':<14} | {'Credentials':<18} | {'Campus':<24} | {'Email':<26} | {'Phone':<15} | {'Title'}")
    print("-" * 140)
    for i, c in enumerate(unique_contacts[:15], 1):
        fn = str(c.get("first_name", ""))[:14]
        ln = str(c.get("last_name", ""))[:14]
        creds = str(c.get("credentials") or c.get("credential") or "")[:18]
        camp = str(c.get("campus", ""))[:24]
        email = str(c.get("email", ""))[:26]
        phone = str(c.get("phone", ""))[:15]
        title = str(c.get("title", ""))[:25]
        print(f"{i:<3} | {fn:<14} | {ln:<14} | {creds:<18} | {camp:<24} | {email:<26} | {phone:<15} | {title}")
    print("-" * 140 + "\n")

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
