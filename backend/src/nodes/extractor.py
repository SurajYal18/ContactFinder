import os
import sys
import json
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from src.state import ScraperState

# Reconfigure stdout encoding to UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Load environment variables
load_dotenv()

# Dedicated Gemini AI model
TARGET_MODEL = "gemini-3.5-flash-lite"

# Calls Gemini AI to extract nursing faculty contacts as structured JSON
def call_gemini_extraction(clean_text: str, user_prompt: str, api_key: str, university: str = "") -> list:
    # System instruction for comprehensive, accurate extraction
    system_instruction = f"""You are an elite data extraction specialist for higher education and university faculty directories.

Task:
Extract ALL Nursing faculty, leadership, instructors, and staff members from the provided webpage content and individual profile bio sections into an Excel-ready dataset.
User prompt: {user_prompt}

Scope & Selection Criteria:
1. Include all individuals affiliated with the School of Nursing, Department of Nursing, or Nursing programs.
2. Include anyone holding nursing or clinical healthcare credentials/degrees (e.g. RN, BSN, MSN, DNP, PhD in Nursing, APRN, FNP, CNE, AGNP, CNS) or teaching nursing courses.
3. Include nursing administrative and student support staff (e.g. Chairs, Directors, Program Coordinators, Skills Lab Coordinators, Advisors).
4. If the page contains a master university directory or table featuring multiple departments (e.g. Biology, Math, Arts, Nursing), extract ONLY the individuals belonging to Nursing or Health Professions. Do NOT include unrelated departments like English, History, Physics, etc.
5. If individual faculty profile bio pages are appended, extract their exact email, direct phone, office location, and education degrees/credentials from their profile section.
6. Extract EVERY single matching person without truncation, omission, or placeholders.

Field Specifications:
- "school": Name of the college, university, or school (e.g., "{university if university else 'the University'}").
- "first_name": Clean first name (remove honorifics such as Dr., Prof., Mr., Ms., Mrs.).
- "last_name": Last name.
- "title": Complete job title, faculty rank, or administrative role (e.g., Professor of Nursing, Interim Chair, Assistant Professor, Lecturer).
- "credentials": All post-nominal degrees, licenses, and certifications (e.g., PhD, DNP, MSN, BSN, RN, FNP-C, APRN). Leave empty string "" if none found.
- "email": Institutional email address (e.g., name@university.edu). If not found, leave empty string "".
- "phone": Direct or office phone number. If not found, leave empty string "".
- "campus": Specific physical campus or branch location (e.g., Statesboro Campus, Armstrong Campus, West Campus). IMPORTANT: If a specific physical campus or branch location is not explicitly stated on the page, leave this as an empty string "". Do NOT put the university or college name here. Keep it blank if campus is not available.

Output Format:
Return ONLY a valid, parseable JSON array of objects with the exact keys:
"school", "first_name", "last_name", "title", "credentials", "email", "phone", "campus"
"""

    # Support both Agent Platform (Vertex AI) API keys and Standard Gemini API keys
    use_agent_platform = True

    # Attempt extraction in one shot using gemini-3.5-flash-lite with retries
    for attempt in range(3):
        try:
            platform_name = "Agent Platform API" if use_agent_platform else "Standard Gemini API"
            print(f"[*] Calling {TARGET_MODEL} via {platform_name} (attempt {attempt+1})...")
            client = genai.Client(vertexai=use_agent_platform, api_key=api_key)
            chat = client.chats.create(
                model=TARGET_MODEL,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            full_prompt = f"{system_instruction}\n\nWebpage Content:\n{clean_text}"
            response = chat.send_message(full_prompt)

            # Log input and output token counts directly to the console
            usage = getattr(response, "usage_metadata", None)
            if usage:
                in_tokens = getattr(usage, "prompt_token_count", 0)
                out_tokens = getattr(usage, "candidates_token_count", 0)
                total_tokens = getattr(usage, "total_token_count", 0)
                print(f"[*] Gemini Token Usage -> Input: {in_tokens:,} tokens | Output: {out_tokens:,} tokens | Total: {total_tokens:,} tokens")

            data = json.loads(response.text)
            if isinstance(data, list) and len(data) > 0:
                print(f"[+] {TARGET_MODEL} successfully extracted {len(data)} nursing contacts in one shot.")
                return data
        except Exception as e:
            err_msg = str(e)
            print(f"[!] {TARGET_MODEL} (attempt {attempt+1}) failed: {err_msg[:120]}")
            # If service is blocked or permission denied, toggle between Agent Platform and Standard Gemini
            if any(term in err_msg for term in ["API_KEY_SERVICE_BLOCKED", "PERMISSION_DENIED", "not been used", "403"]):
                use_agent_platform = not use_agent_platform
            # Exponential backoff for rate limits or transient errors
            time.sleep((attempt + 1) * 2)

    return []

# LangGraph node for extracting faculty directory records using Gemini AI
def extract_node(state: ScraperState) -> ScraperState:
    # Halt step if an error occurred in previous nodes
    if state.get("error"):
        return state

    clean_text = state.get("clean_text", "")
    if not clean_text:
        return {"error": "No cleaned text available for extraction", "status": "Extraction failed: empty text"}

    user_prompt = state.get("user_prompt") or "create an excel file of nursing faculty - include school, first name, last name, title, credentials, email, phone, and campus"
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    university = state.get("university", "").strip()

    if not gemini_key:
        return {"error": "GEMINI_API_KEY missing from environment", "status": "Extraction failed: missing API key"}

    contacts = call_gemini_extraction(clean_text, user_prompt, gemini_key, university=university)

    # Normalize fields:
    # 1. School: include name of college or university or school
    # 2. Campus: keep blank if campus not available; do NOT use university name!
    for c in contacts:
        sch = str(c.get("school") or "").strip()
        if not sch or sch.lower() in ["none", "null", "n/a", "undefined", "-"]:
            c["school"] = university or ""
        else:
            c["school"] = sch

        camp = str(c.get("campus") or "").strip()
        camp_lower = camp.lower()
        sch_lower = c["school"].lower()
        uni_lower = university.lower()

        if (not camp or 
            camp_lower in ["none", "null", "n/a", "undefined", "unknown", "-"] or 
            (uni_lower and camp_lower == uni_lower) or 
            (sch_lower and camp_lower == sch_lower)):
            c["campus"] = ""
        else:
            c["campus"] = camp

    return {
        "contacts": contacts,
        "status": f"Extracted {len(contacts)} contacts via Gemini AI"
    }
