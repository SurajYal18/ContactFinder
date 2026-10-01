# FastAPI server exposing endpoints for LangGraph university directory scraper and Excel exporter
import os
import re
import sys
import uuid
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Reconfigure stdout encoding to UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    load_dotenv()

# Import LangGraph workflow and helper functions
from src.workflow import create_scraper_graph
from src.nodes.exporter import export_contacts_to_excel

# Create FastAPI application instance
app = FastAPI(
    title="Contact Finder API",
    description="Automated contact and email discovery service",
    version="2.0.0"
)

# Enable Cross-Origin Resource Sharing (CORS) for all frontend clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import tempfile

# Ensure directory for exported spreadsheets exists (use /tmp in serverless environments like Vercel)
if os.getenv("VERCEL") or not os.access(os.path.dirname(os.path.abspath(__file__)), os.W_OK):
    EXPORTS_DIR = os.path.join(tempfile.gettempdir(), "exports")
else:
    EXPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)

# Default extraction prompt
DEFAULT_PROMPT = "create an excel file of nursing faculty - include first name, last name, credentials, title, campus, email and phone"

# Pydantic schema for scraping request payload
class ScrapeRequest(BaseModel):
    # List of university directory URLs to process
    urls: List[str] = Field(..., description="One or more university directory URLs")
    # Custom prompt guiding Gemini AI extraction
    prompt: Optional[str] = Field(default=DEFAULT_PROMPT, description="Extraction prompt for Gemini AI")
    # Max contacts to enrich with Hunter.io
    enrich_limit: Optional[int] = Field(default=0, description="Max contacts to enrich with Hunter.io")
    # Custom output Excel filename
    output_filename: Optional[str] = Field(default=None, description="Optional custom output filename")

# Normalizes contact for deduplication
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

# Health check and environment configuration endpoint
@app.get("/api/health")
def health_check():
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    hunter_key = os.getenv("HUNTER_API_KEY", "").strip()
    return {
        "status": "online",
        "gemini_configured": bool(gemini_key),
        "hunter_configured": bool(hunter_key),
        "enable_hunter": os.getenv("ENABLE_HUNTER", "true").lower() in ["true", "1", "yes"]
    }

# Main scraping and extraction endpoint
@app.post("/api/scrape")
def scrape_directories(req: ScrapeRequest):
    if not req.urls:
        raise HTTPException(status_code=400, detail="At least one URL must be provided.")

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured in environment.")

    # Flatten and clean URLs list
    cleaned_urls = []
    for u in req.urls:
        for sub in u.split(","):
            sub = sub.strip()
            if sub and sub.startswith("http"):
                cleaned_urls.append(sub)

    if not cleaned_urls:
        raise HTTPException(status_code=400, detail="No valid HTTP/HTTPS URLs found in request.")

    # Compile the LangGraph pipeline
    compiled_graph = create_scraper_graph()
    all_contacts = []
    url_logs = []

    # Process each URL through the LangGraph StateGraph
    for idx, url in enumerate(cleaned_urls, 1):
        initial_state = {
            "url": url,
            "user_prompt": req.prompt or DEFAULT_PROMPT,
            "enrich_limit": req.enrich_limit
        }
        try:
            final_state = compiled_graph.invoke(initial_state)
            if final_state.get("error"):
                err_msg = str(final_state["error"])
                if "403" in err_msg or "cloudflare" in err_msg.lower():
                    clean_msg = "Access blocked: This website is protected by Cloudflare bot protection (HTTP 403 Forbidden)."
                    url_logs.append({"url": url, "status": "blocked", "message": clean_msg, "error": err_msg})
                else:
                    url_logs.append({"url": url, "status": "error", "message": err_msg})
                continue

            extracted = final_state.get("contacts", [])
            all_contacts.extend(extracted)
            if len(extracted) == 0:
                url_logs.append({
                    "url": url,
                    "status": "no_contacts",
                    "message": "No nursing faculty or staff contacts were found for this directory URL.",
                    "university": final_state.get("university", ""),
                    "count": 0
                })
            else:
                url_logs.append({
                    "url": url,
                    "status": "success",
                    "message": f"Found {len(extracted)} nursing contacts.",
                    "university": final_state.get("university", ""),
                    "count": len(extracted)
                })
        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg or "cloudflare" in err_msg.lower():
                clean_msg = "Access blocked: This website is protected by Cloudflare bot protection (HTTP 403 Forbidden)."
                url_logs.append({"url": url, "status": "blocked", "message": clean_msg, "error": err_msg})
            else:
                url_logs.append({"url": url, "status": "exception", "message": err_msg})

    # Deduplicate accumulated contacts
    unique_contacts = deduplicate_contacts(all_contacts)

    # Determine status and user-facing message
    if len(unique_contacts) > 0:
        res_status = "success"
        res_message = f"Successfully extracted {len(unique_contacts)} unique contacts."
    elif any(l.get("status") == "blocked" for l in url_logs):
        res_status = "blocked"
        res_message = "Access blocked: This website is protected by Cloudflare bot protection (HTTP 403 Forbidden)."
    else:
        res_status = "no_contacts"
        res_message = "No nursing faculty or staff contacts were found for this directory URL."

    # Determine Excel output file path
    unique_id = uuid.uuid4().hex[:8]
    base_name = req.output_filename or f"contacts_export_{unique_id}.xlsx"
    if not base_name.endswith(".xlsx"):
        base_name += ".xlsx"

    excel_path = os.path.join(EXPORTS_DIR, base_name)
    export_contacts_to_excel(unique_contacts, excel_path)

    return {
        "status": res_status,
        "message": res_message,
        "total_extracted": len(all_contacts),
        "unique_contacts": len(unique_contacts),
        "contacts": unique_contacts,
        "filename": base_name,
        "download_url": f"/api/download/{base_name}",
        "url_logs": url_logs
    }

# Endpoint to download generated Excel spreadsheet
@app.get("/api/download/{filename}")
def download_excel(filename: str):
    # Sanitize filename against directory traversal
    clean_name = os.path.basename(filename)
    file_path = os.path.join(EXPORTS_DIR, clean_name)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Requested spreadsheet was not found.")
    return FileResponse(
        path=file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=clean_name
    )

# Frontend web UI directory path
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))

# Mount frontend static single page application if directory exists
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

# Direct execution runner
if __name__ == "__main__":
    import uvicorn
    # Start ASGI web server on port 8000
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
