# LangGraph node to extract university nursing faculty via Gemini AI in one shot without chunking
import os
import json
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from src.state import ScraperState

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
- "first_name": Clean first name (remove honorifics such as Dr., Prof., Mr., Ms., Mrs.).
- "last_name": Last name.
- "credentials": All post-nominal degrees, licenses, and certifications (e.g., PhD, DNP, MSN, BSN, RN, FNP-C, APRN). Leave empty string "" if none found.
- "title": Complete job title, faculty rank, or administrative role (e.g., Professor of Nursing, Interim Chair, Assistant Professor, Lecturer).
- "campus": Specific campus name, branch, or office location (e.g., Statesboro Campus, Armstrong Campus, West Campus). If specific campus detail is not available, use the University or College name: {university if university else 'the University'}.
- "email": Institutional email address (e.g., name@university.edu). If not found, leave empty string "".
- "phone": Direct or office phone number. If not found, leave empty string "".

Output Format:
Return ONLY a valid, parseable JSON array of objects with the exact keys:
"first_name", "last_name", "credentials", "title", "campus", "email", "phone"
"""

    client = genai.Client(api_key=api_key)

    # Attempt extraction in one shot using gemini-3.5-flash-lite with retries
    for attempt in range(3):
        try:
            print(f"[*] Calling Gemini AI model ({TARGET_MODEL}) in ONE SHOT (attempt {attempt+1})...")
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
                print(f"[✓] {TARGET_MODEL} successfully extracted {len(data)} nursing contacts in one shot.")
                return data
        except Exception as e:
            err_msg = str(e)
            print(f"[!] {TARGET_MODEL} (attempt {attempt+1}) failed: {err_msg[:90]}")
            time.sleep(2)

    return []

# LangGraph node for extracting faculty directory records using Gemini AI
def extract_node(state: ScraperState) -> ScraperState:
    # Halt step if an error occurred in previous nodes
    if state.get("error"):
        return state

    clean_text = state.get("clean_text", "")
    if not clean_text:
        return {"error": "No cleaned text available for extraction", "status": "Extraction failed: empty text"}

    user_prompt = state.get("user_prompt") or "create an excel file of nursing faculty - include first name, last name, credentials, title, campus, email and phone"
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    university = state.get("university", "").strip()

    if not gemini_key:
        return {"error": "GEMINI_API_KEY missing from environment", "status": "Extraction failed: missing API key"}

    contacts = call_gemini_extraction(clean_text, user_prompt, gemini_key, university=university)

    # Ensure university fallback is applied if campus detail is not available
    for c in contacts:
        camp = str(c.get("campus") or "").strip()
        if not camp or camp.lower() in ["none", "null", "n/a", "undefined"]:
            if university:
                c["campus"] = university

    return {
        "contacts": contacts,
        "status": f"Extracted {len(contacts)} contacts via Gemini AI"
    }
