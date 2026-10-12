import os
import re
import sys
import uuid
import hmac
import hashlib
import time
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Reconfigure stdout encoding to UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure backend directory is in sys.path for local and serverless execution
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Load environment variables
dotenv_path = os.path.join(backend_dir, ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    load_dotenv()

# Import LangGraph workflow and helper functions
from urllib.parse import urlparse
from src.workflow import create_scraper_graph
from src.nodes.fetcher import get_university_name
from src.nodes.exporter import export_contacts_to_excel
from src.finder import (
    find_directory_for_school,
    extract_schools_from_text,
    extract_schools_from_pdf,
    extract_schools_from_excel,
    deduplicate_school_names
)

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
DEFAULT_PROMPT = "create an excel file of nursing faculty - include school, first name, last name, title, credentials, email, phone, and campus"

# Pydantic schema for scraping request payload
class ScrapeRequest(BaseModel):
    # List of university directory URLs to process
    urls: List[str] = Field(..., description="One or more university directory URLs")
    # Custom prompt guiding Gemini AI extraction
    prompt: Optional[str] = Field(default=DEFAULT_PROMPT, description="Extraction prompt for Gemini AI")
    # Email enrichment provider selection: "hunter", "rocketreach", "both", or "none"
    enrich_provider: Optional[str] = Field(default="hunter", description="Email enrichment provider: hunter, rocketreach, both, or none")
    # Max contacts to enrich with external APIs
    enrich_limit: Optional[int] = Field(default=0, description="Max contacts to enrich with external APIs")
    # Custom output Excel filename
    output_filename: Optional[str] = Field(default=None, description="Optional custom output filename")
    
# Request schema for directory discovery
class DirectorySearchRequest(BaseModel):
    schools: Optional[List[str]] = Field(default=[], description="List of institution names")
    text: Optional[str] = Field(default="", description="Raw pasted text containing institution names")
    concurrency: Optional[int] = Field(default=5, description="Number of concurrent discovery workers")

# Normalizes contact for deduplication
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

# Authentication Request Schema
class LoginRequest(BaseModel):
    username: str
    password: str

# Retrieve authentication configuration from environment
# Retrieve authentication configuration exclusively from environment variables (.env)
def get_auth_credentials():
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path, override=True)
    user = os.getenv("AUTH_USERNAME", "").strip()
    pwd = os.getenv("AUTH_PASSWORD", "").strip()
    secret = os.getenv("AUTH_SECRET_KEY", "contact-collector-secret-key-2026").strip()
    return user, pwd, secret

# Generate a cryptographically signed session token (7-day validity)
def generate_auth_token(username: str) -> str:
    user, pwd, secret = get_auth_credentials()
    timestamp = int(time.time())
    payload = f"{username}:{timestamp}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

# Validate session token signature and expiration
def verify_auth_token(token: Optional[str]) -> bool:
    if not token:
        return False
    parts = token.strip().split(":")
    if len(parts) != 3:
        return False
    username, timestamp_str, signature = parts
    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return False

    user, pwd, secret = get_auth_credentials()
    if not user or username != user:
        return False

    # Check 7-day expiration
    now = time.time()
    if (now - timestamp) > (7 * 24 * 3600) or timestamp > (now + 300):
        return False

    expected_payload = f"{username}:{timestamp_str}"
    expected_sig = hmac.new(secret.encode("utf-8"), expected_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected_sig)

# Extract token from Authorization header, custom header, cookie, or query param
def get_current_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    if request.headers.get("X-Auth-Token"):
        return request.headers.get("X-Auth-Token").strip()
    if request.cookies.get("auth_token"):
        return request.cookies.get("auth_token").strip()
    if "token" in request.query_params:
        return request.query_params["token"].strip()
    return None

# Verifies incoming request authentication
def verify_request_auth(request: Request) -> bool:
    token = get_current_token(request)
    return verify_auth_token(token)

# Health check and environment configuration endpoint
@app.get("/api/health")
@app.get("/health")
def health_check():
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path, override=True)
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    hunter_key = os.getenv("HUNTER_API_KEY", "").strip()
    rocketreach_key = os.getenv("ROCKETREACH_API_KEY", "").strip() or os.getenv("ROCKETSEARCH_API_KEY", "").strip()
    enable_hunter = os.getenv("ENABLE_HUNTER", "true").lower() in ["true", "1", "yes"]
    enable_rocketreach = (
        os.getenv("ENABLE_ROCKETREACH", "true").lower() in ["true", "1", "yes"] and
        os.getenv("ENABLE_ROCKETSEARCH", "true").lower() in ["true", "1", "yes"]
    )
    return {
        "status": "online",
        "auth_required": True,
        "gemini_configured": bool(gemini_key),
        "hunter_configured": bool(hunter_key),
        "enable_hunter": enable_hunter,
        "rocketreach_configured": bool(rocketreach_key),
        "enable_rocketreach": enable_rocketreach
    }

# Authenticates user against AUTH_USERNAME and AUTH_PASSWORD in .env
@app.post("/api/login")
@app.post("/login")
def login_endpoint(payload: LoginRequest, response: Response):
    user, pwd, _ = get_auth_credentials()
    if not user or not pwd:
        raise HTTPException(
            status_code=500,
            detail="Authentication is not configured on the server. Please set AUTH_USERNAME and AUTH_PASSWORD in .env"
        )
    if payload.username.strip() == user and payload.password.strip() == pwd:
        token = generate_auth_token(user)
        response.set_cookie(
            key="auth_token",
            value=token,
            max_age=7 * 24 * 3600,
            httponly=False,
            samesite="lax",
            path="/"
        )
        return {
            "success": True,
            "message": "Login successful",
            "token": token,
            "username": user
        }
    raise HTTPException(status_code=401, detail="Invalid username or password.")

# Verifies current session token
@app.get("/api/verify-auth")
@app.get("/verify-auth")
def verify_auth_endpoint(request: Request):
    token = get_current_token(request)
    is_valid = verify_auth_token(token)
    user, _, _ = get_auth_credentials()
    return {
        "authenticated": is_valid,
        "username": user if is_valid else None
    }

# Clears authentication cookie
@app.post("/api/logout")
@app.post("/logout")
def logout_endpoint(response: Response):
    response.delete_cookie(key="auth_token", path="/")
    return {"success": True, "message": "Logged out successfully"}

# Maximum simultaneous requests for scraping and Gemini AI extraction
MAX_CONCURRENT_REQUESTS = 5

# Processes a single directory URL through the LangGraph StateGraph pipeline
def process_single_url(url: str, prompt: str, enrich_provider: str, enrich_limit: Optional[int], compiled_graph) -> dict:
    domain = urlparse(url).netloc.lower()
    fallback_school = get_university_name(domain)

    initial_state = {
        "url": url,
        "user_prompt": prompt or DEFAULT_PROMPT,
        "enrich_provider": enrich_provider or "hunter",
        "enrich_limit": enrich_limit
    }
    try:
        final_state = compiled_graph.invoke(initial_state)
        university = final_state.get("university", "").strip() or fallback_school

        if final_state.get("error"):
            err_msg = str(final_state["error"])
            if "403" in err_msg or "cloudflare" in err_msg.lower() or "access blocked" in err_msg.lower():
                reason = "Cloudflare Protected (HTTP 403)"
                clean_msg = "Access blocked by Cloudflare bot protection."
                status_type = "blocked"
            elif "pdf documents are not supported" in err_msg.lower() or url.lower().endswith(".pdf"):
                reason = "PDF Document (Not have email and phone number)"
                clean_msg = "PDF extraction is disabled. Please provide a web faculty directory page instead."
                status_type = "pdf_unsupported"
            elif "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                reason = "Connection Timeout"
                clean_msg = "Server took too long to respond or blocked automated connection."
                status_type = "timeout"
            elif "ssl" in err_msg.lower() or "certificate" in err_msg.lower():
                reason = "SSL Certificate Error"
                clean_msg = "Website has an invalid or expired SSL certificate."
                status_type = "ssl_error"
            else:
                reason = "Fetch Error"
                clean_msg = err_msg
                status_type = "error"

            entry = {
                "url": url,
                "school": university,
                "status": status_type,
                "reason": reason,
                "message": clean_msg,
                "error": err_msg
            }
            return {"contacts": [], "entry": entry, "is_success": False}

        extracted = final_state.get("contacts", [])
        for c in extracted:
            if not c.get("school"):
                c["school"] = university or ""

        if len(extracted) == 0:
            parsed_p = urlparse(url).path.strip("/")
            if not parsed_p:
                reason = "Homepage (Not a Directory)"
                clean_msg = "This URL points to the university homepage rather than a faculty directory."
            elif url.lower().endswith(".pdf"):
                reason = "PDF (No Nursing Contacts)"
                clean_msg = "PDF document parsed but contained no nursing faculty."
            else:
                reason = "No Nursing Faculty Found"
                clean_msg = "No nursing faculty or staff contacts were found on this directory page."

            entry = {
                "url": url,
                "school": university,
                "status": "no_contacts",
                "reason": reason,
                "message": clean_msg,
                "university": university,
                "count": 0
            }
            return {"contacts": [], "entry": entry, "is_success": False}
        else:
            entry = {
                "url": url,
                "school": university,
                "status": "success",
                "reason": None,
                "message": f"Found {len(extracted)} nursing contacts.",
                "university": university,
                "count": len(extracted)
            }
            return {"contacts": extracted, "entry": entry, "is_success": True}

    except Exception as e:
        err_msg = str(e)
        if "403" in err_msg or "cloudflare" in err_msg.lower() or "access blocked" in err_msg.lower():
            reason = "Cloudflare Protected (HTTP 403)"
            clean_msg = "Access blocked by Cloudflare bot protection."
            status_type = "blocked"
        elif "pdf documents are not supported" in err_msg.lower() or url.lower().endswith(".pdf"):
            reason = "PDF Document (Not have email and phone number)"
            clean_msg = "PDF extraction is disabled. Please provide a web faculty directory page instead."
            status_type = "pdf_unsupported"
        elif "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
            reason = "Connection Timeout"
            clean_msg = "Connection timed out."
            status_type = "timeout"
        else:
            reason = "Failed to Process"
            clean_msg = err_msg
            status_type = "exception"

        entry = {
            "url": url,
            "school": fallback_school,
            "status": status_type,
            "reason": reason,
            "message": clean_msg,
            "error": err_msg
        }
        return {"contacts": [], "entry": entry, "is_success": False}

# Main scraping and extraction endpoint
@app.post("/api/scrape")
@app.post("/scrape")
def scrape_directories(req: ScrapeRequest, request: Request):
    if not verify_request_auth(request):
        raise HTTPException(status_code=401, detail="Unauthorized. Please log in.")
    if not req.urls:
        raise HTTPException(status_code=400, detail="At least one URL must be provided.")

    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path, override=True)
    else:
        load_dotenv(override=True)

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured in environment.")

    # Flatten and clean URLs list, while collecting any raw school names
    cleaned_urls = []
    potential_schools = []
    for u in req.urls:
        for sub in u.split("\n"):
            sub = sub.strip()
            if not sub:
                continue
            m = re.search(r'https?://[^\s"\'\]\)>]+', sub)
            if m:
                clean_link = m.group(0).rstrip('"\'.,;:)')
                cleaned_urls.append(clean_link)
            elif sub.startswith("http"):
                cleaned_urls.append(sub.strip().strip('"\'[]<>'))
            elif sub.startswith("www."):
                cleaned_urls.append("https://" + sub.strip())
            elif len(sub) >= 3:
                potential_schools.append(sub)

    if not cleaned_urls:
        if potential_schools:
            raise HTTPException(
                status_code=400,
                detail="Contact Finder strictly accepts faculty directory URLs (e.g., https://...). To search by school names or upload documents, please switch to the Directory Finder tab."
            )
        raise HTTPException(
            status_code=400,
            detail="No valid HTTP/HTTPS URLs found in request. Please paste faculty directory URLs (e.g., https://...)."
        )

    # Compile the LangGraph pipeline
    compiled_graph = create_scraper_graph()
    all_contacts = []
    completed_results = []
    effective_workers = min(MAX_CONCURRENT_REQUESTS, len(cleaned_urls))

    # Process up to 5 URLs concurrently through the LangGraph StateGraph pipeline
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_idx = {
            executor.submit(
                process_single_url,
                url,
                req.prompt or DEFAULT_PROMPT,
                req.enrich_provider or "hunter",
                req.enrich_limit,
                compiled_graph
            ): idx
            for idx, url in enumerate(cleaned_urls)
        }

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                res = future.result()
                completed_results.append((idx, res))
            except Exception as e:
                err_msg = str(e)
                domain = urlparse(cleaned_urls[idx]).netloc.lower()
                fallback_school = get_university_name(domain)
                completed_results.append((idx, {
                    "contacts": [],
                    "entry": {
                        "url": cleaned_urls[idx],
                        "school": fallback_school,
                        "status": "exception",
                        "reason": "Failed to Process",
                        "message": err_msg,
                        "error": err_msg
                    },
                    "is_success": False
                }))

    # Sort results to preserve the original URL request order in logs
    completed_results.sort(key=lambda x: x[0])

    url_logs = []
    failed_urls = []
    successful_urls = []
    for _, item in completed_results:
        contacts = item.get("contacts", [])
        if contacts:
            all_contacts.extend(contacts)
        entry = item.get("entry", {})
        url_logs.append(entry)
        if item.get("is_success"):
            successful_urls.append(entry)
        else:
            failed_urls.append(entry)

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

    # Determine export filename for client-side download
    unique_id = uuid.uuid4().hex[:8]
    base_name = req.output_filename or f"contacts_export_{unique_id}.xlsx"
    if not base_name.endswith(".xlsx"):
        base_name += ".xlsx"

    # Client-side SheetJS directly handles Excel generation and downloads (zero server storage/backup)
    return {
        "status": res_status,
        "message": res_message,
        "total_extracted": len(all_contacts),
        "unique_contacts": len(unique_contacts),
        "unique_extracted": len(unique_contacts),
        "successful_count": len(successful_urls),
        "failed_count": len(failed_urls),
        "contacts": unique_contacts,
        "filename": base_name,
        "download_url": "",
        "url_logs": url_logs,
        "results": url_logs,
        "failed_urls": failed_urls,
        "successful_urls": successful_urls
    }

# Endpoint to download generated Excel spreadsheet
@app.get("/api/download/{filename}")
@app.get("/download/{filename}")
def download_excel(filename: str):
    # Sanitize filename against directory traversal
    clean_name = os.path.basename(filename)
    candidate_paths = [
        os.path.join(EXPORTS_DIR, clean_name),
        os.path.join(tempfile.gettempdir(), "exports", clean_name),
        os.path.join(tempfile.gettempdir(), clean_name),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports", clean_name)
    ]
    file_path = next((p for p in candidate_paths if os.path.isfile(p)), None)
    if not file_path:
        raise HTTPException(status_code=404, detail="Requested spreadsheet was not found.")
    return FileResponse(
        path=file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=clean_name
    )

# ==========================================
# Directory Finder Endpoints
# ==========================================
@app.post("/api/find-directories")
@app.post("/find-directories")
def find_directories_endpoint(payload: DirectorySearchRequest, request: Request):
    if not verify_request_auth(request):
        raise HTTPException(status_code=401, detail="Unauthorized. Please log in.")
    schools = []
    if payload.text and payload.text.strip():
        schools.extend(extract_schools_from_text(payload.text))
    if payload.schools:
        schools.extend(payload.schools)

    schools = deduplicate_school_names(schools)
    if not schools:
        raise HTTPException(status_code=400, detail="No valid school names provided.")

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    workers = min(max(payload.concurrency, 1), 10)
    results = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(find_directory_for_school, s, gemini_key): s for s in schools}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                s_name = futures[future]
                results.append({
                    "school": s_name,
                    "directory_url": "",
                    "status": f"Error: {e}",
                    "is_ready_for_extractor": False
                })

    # Deduplicate results by normalized directory URL and school name
    deduped = []
    seen_urls = set()
    seen_schools = set()
    for r in results:
        u = (r.get("directory_url") or "").rstrip('/').lower()
        s = (r.get("school") or "").strip().lower()
        if u and u in seen_urls:
            continue
        if s and s in seen_schools:
            continue
        if u:
            seen_urls.add(u)
        if s:
            seen_schools.add(s)
        deduped.append(r)

    results = deduped
    results.sort(key=lambda x: x.get("school", ""))
    verified_count = sum(1 for r in results if r.get("is_ready_for_extractor"))
    waf_count = sum(1 for r in results if "waf" in str(r.get("status", "")).lower() or "protected" in str(r.get("status", "")).lower() or "cloudflare" in str(r.get("status", "")).lower())

    for r in results:
        r["url"] = r.get("directory_url", "")
        r["ready"] = bool(r.get("is_ready_for_extractor", False))

    return {
        "success": True,
        "total": len(results),
        "verified_count": verified_count,
        "ready_count": verified_count,
        "waf_count": waf_count,
        "results": results,
        "directories": results
    }

try:
    import multipart  # noqa: F401
    MULTIPART_AVAILABLE = True
except ImportError:
    MULTIPART_AVAILABLE = False

if MULTIPART_AVAILABLE:
    @app.post("/api/extract-schools-from-file")
    @app.post("/extract-schools-from-file")
    async def extract_schools_from_file_endpoint(request: Request, file: UploadFile = File(...)):
        if not verify_request_auth(request):
            raise HTTPException(status_code=401, detail="Unauthorized. Please log in.")
        content = await file.read()
        fname = (file.filename or "upload").lower()
        suffix = os.path.splitext(fname)[1] or ".tmp"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=tempfile.gettempdir()) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            if fname.endswith(".pdf"):
                schools = extract_schools_from_pdf(tmp_path)
            elif fname.endswith((".xlsx", ".xls", ".csv")):
                schools = extract_schools_from_excel(tmp_path)
            else:
                text = content.decode("utf-8", errors="ignore")
                schools = extract_schools_from_text(text)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        return {
            "filename": file.filename,
            "count": len(schools),
            "schools": schools
        }
else:
    @app.post("/api/extract-schools-from-file")
    @app.post("/extract-schools-from-file")
    async def extract_schools_from_file_fallback(request: Request):
        if not verify_request_auth(request):
            raise HTTPException(status_code=401, detail="Unauthorized. Please log in.")
        raise HTTPException(
            status_code=501,
            detail="File upload requires python-multipart to be installed on the server."
        )

# Frontend web UI directory paths
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
INDEX_HTML = os.path.join(FRONTEND_DIR, "index.html")
DIRECTORY_HTML = os.path.join(FRONTEND_DIR, "directory.html")
LOGIN_HTML = os.path.join(FRONTEND_DIR, "login.html")

# Serve login.html with strict no-cache headers
@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
@app.get("/login.html", response_class=HTMLResponse, include_in_schema=False)
def serve_login():
    if os.path.isfile(LOGIN_HTML):
        with open(LOGIN_HTML, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(
            content=content,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    raise HTTPException(status_code=404, detail="Login page not found")

# Serve index.html (Contact Finder) with strict no-cache headers
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/index.html", response_class=HTMLResponse, include_in_schema=False)
def serve_index():
    if os.path.isfile(INDEX_HTML):
        with open(INDEX_HTML, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(
            content=content,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    raise HTTPException(status_code=404, detail="Frontend not found")

# Serve directory.html (Directory Finder) with strict no-cache headers
@app.get("/directory", response_class=HTMLResponse, include_in_schema=False)
@app.get("/directory.html", response_class=HTMLResponse, include_in_schema=False)
def serve_directory():
    if os.path.isfile(DIRECTORY_HTML):
        with open(DIRECTORY_HTML, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(
            content=content,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    raise HTTPException(status_code=404, detail="Directory Finder page not found")

# Mount any other static assets (images, icons etc.) from the frontend folder
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="frontend_static")

# Direct execution runner
if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "false").lower() in ["true", "1"]
    print(f"[*] Starting Contact Finder server on {host}:{port} (reload={reload})...")
    uvicorn.run("server:app", host=host, port=port, reload=reload)

