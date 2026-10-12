#!/usr/bin/env python3
"""
ContactFinder - Directory Finder Tool
------------------------------------
Automatically extracts university/college names from PDFs, Excel sheets, or text lists,
searches for official Nursing Faculty Directory URLs using Gemini AI + curated registry,
live-verifies each URL (avoiding 404s and non-directory homepages), and outputs an Excel spreadsheet
and a clean URL list ready to paste directly into https://contactfinder-zeta.vercel.app/
"""

import os
import sys
import re
import io
import argparse
import tempfile
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import urllib3
import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Reconfigure stdout encoding to UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Suppress insecure SSL warnings for institutions with self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ensure backend directory is in sys.path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Load environment variables
dotenv_path = os.path.join(BACKEND_DIR, ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    load_dotenv()

# Browser headers for HTTP validation
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
}

# Known verified directory links
KNOWN_VERIFIED = {
    'abraham baldwin agricultural college': 'https://www.abac.edu/academics/schools/nursing_health_natural_sciences/index.html',
    'albany state university': 'https://www.asurams.edu/academic-affairs/dchealthprof/facultystaff-directory.php',
    'albany technical college': 'https://www.albanytech.edu/faculty-and-staff',
    'andrew college': 'https://www.andrewcollege.edu/nursing/',
    'athens technical college': 'https://athenstech.edu/about/directory/',
    'atlanta technical college': 'https://www.atlantatech.edu/directory/',
    'augusta technical college': 'https://www.augustatech.edu/student-resources/faculty-and-staff-directory.cms',
    'augusta university': 'https://www.augusta.edu/faculty/directory/',
    'averett university': 'https://www.averett.edu/faculty-staff/',
    'averett': 'https://www.averett.edu/faculty-staff/',
    'berry college': 'https://www.berry.edu/academics/fs/departments/nursing',
    'brenau university': 'https://www.brenau.edu/health-sciences/nursing/',
    'brewton-parker christian university': 'https://bpc.edu/divisions/nursing/',
    'brewton-parker': 'https://bpc.edu/divisions/nursing/',
    'cambridge college of healthcare and technology': 'https://www.cambridgehealth.edu/campus-contacts/',
    'cambridge college': 'https://www.cambridgehealth.edu/campus-contacts/',
    'central georgia technical college': 'https://www.centralgatech.edu/faculty-staff-directory',
    'central georgia tech': 'https://www.centralgatech.edu/faculty-staff-directory',
    'chamberlain university': 'https://www.chamberlain.edu/nursing-school/georgia',
    'chatta': 'https://www.chattahoocheetech.edu/contact-us/directory.html',
    'chattahoochee technical college': 'https://www.chattahoocheetech.edu/contact-us/directory.html',
    'clayton state university': 'https://www.clayton.edu/health/nursing/faculty-and-research.php',
    'clayton state': 'https://www.clayton.edu/health/nursing/faculty-and-research.php',
    'coastal pines technical college': 'https://coastalpines.edu/faculty-staff-directory',
    'coastal pines': 'https://coastalpines.edu/faculty-staff-directory',
    'college of coastal georgia': 'https://www.ccga.edu/about/directory/',
    'columbus state university': 'https://www.columbusstate.edu/nursing/contact-us.php',
    'columbus state': 'https://www.columbusstate.edu/nursing/contact-us.php',
    'columbus technical college': 'https://www.columbustech.edu/directory/',
    'dalton state college': 'https://www.daltonstate.edu/academics/schools-and-departments/school-of-health-professions/',
    'emory university': 'https://nursing.emory.edu/faculty-staff',
    'emory': 'https://nursing.emory.edu/faculty-staff',
    'fort valley state university': 'https://www.fvsu.edu/academics/college-of-arts-and-sciences/department-of-nursing/faculty-staff/',
    'fort valley state': 'https://www.fvsu.edu/academics/college-of-arts-and-sciences/department-of-nursing/faculty-staff/',
    'mercer university': 'https://nursing.mercer.edu/faculty-and-staff/',
    'georgia baptist college of nursing': 'https://nursing.mercer.edu/faculty-and-staff/',
    'georgia college and state university': 'https://www.gcsu.edu/health/nursing/faculty-staff',
    'georgia college': 'https://www.gcsu.edu/health/nursing/faculty-staff',
    'georgia gwinnett college': 'https://www.ggc.edu/directory',
    'georgia highlands college': 'https://sites.highlands.edu/nursing/faculty-and-staff/index.php',
    'georgia northwestern technical college': 'https://www.gntc.edu/directory/',
    'georgia southern university': 'https://www.georgiasouthern.edu/colleges/health-professions/departments/nursing/faculty-and-staff',
    'georgia southwestern state university': 'https://www.gsw.edu/college-of-nursing-and-health-sciences/school-of-nursing/faculty-staff',
    'georgia southwestern': 'https://www.gsw.edu/college-of-nursing-and-health-sciences/school-of-nursing/faculty-staff',
    'georgia state university': 'https://lewis.gsu.edu/profile/nursing/',
    'gordon state college': 'https://www.gordonstate.edu/academics/school-nursing-health-sciences/index.html',
    'gwinnett technical college': 'https://gwinnetttech.edu/faculty-and-staff/',
    'herzing university': 'https://www.herzing.edu/atlanta',
    'kennesaw state university': 'https://campus.kennesaw.edu/colleges-departments/wellstar/academics/nursing/',
    'kennesaw state': 'https://campus.kennesaw.edu/colleges-departments/wellstar/academics/nursing/',
    'lagrange college': 'https://www.lagrange.edu/Faculty-Directory/index.html',
    'lanier technical college': 'https://www.laniertech.edu/faculty-staff/',
    'middle georgia state university': 'https://www.mga.edu/health-natural-sciences/nursing/directory.php',
    'middle georgia': 'https://www.mga.edu/health-natural-sciences/nursing/directory.php',
    'north georgia technical college': 'https://northgatech.edu/faculty-and-staff-directory/',
    'oconee fall line technical college': 'https://www.oftc.edu/staff-directory/',
    'ogeechee technical college': 'https://www.ogeecheetech.edu/academics/nursing',
    'piedmont university': 'https://www.piedmont.edu/program/nursing/',
    'reinhardt university': 'https://catalog.reinhardt.edu/personnel',
    'shorter university': 'https://www.shorter.edu/nursing/',
    'south college': 'https://www.south.edu/faculty-staff/',
    'south georgia state college': 'https://www.sgsc.edu/academics/school-nursing',
    'south university': 'https://www.southuniversity.edu/faculty',
    'southeastern technical college': 'https://catalog.southeasterntech.edu/faculty-staff-directory/',
    'southern crescent technical college': 'https://directory.sctech.edu',
    'southern regional technical college': 'https://southernregionaltech.edu/directory',
    'toccoa falls college': 'https://tfc.edu/directory/',
    'truett-mcconnell university': 'https://truett.edu/directory/',
    'university of north georgia': 'https://ung.edu/nursing/contact.php',
    'university of west georgia': 'https://www.westga.edu/academics/nursing/faculty-staff.php',
    'valdosta state university': 'https://www.valdosta.edu/colleges/nursing-and-health-sciences/conhs-faculty-and-staff.php',
    'valdosta state': 'https://www.valdosta.edu/colleges/nursing-and-health-sciences/conhs-faculty-and-staff.php',
    'vanderbilt university': 'https://nursing.vanderbilt.edu/about/faculty/',
    'vanderbilt': 'https://nursing.vanderbilt.edu/about/faculty/',
    'wesleyan college': 'https://www.wesleyancollege.edu/academics/faculty-listing.cfm',
    'west georgia technical college': 'https://www.westgatech.edu/department/school-of-nursing/',
    'west georgia tech': 'https://www.westgatech.edu/department/school-of-nursing/',
    'wiregrass georgia technical college': 'https://www.wiregrass.edu/about/directory',
    'chipola college': 'https://www.chipola.edu/academics/academic-departments/health-sciences/nursing/',
    'chipola': 'https://www.chipola.edu/academics/academic-departments/health-sciences/nursing/',
    'eastern florida state college': 'https://www.easternflorida.edu/academics/health-sciences/nursing/',
    'emerald coast technical college': 'https://www.ectc.edu/',
    'florida panhandle technical college': 'https://www.fptc.edu/',
    'florida state university': 'https://nursing.fsu.edu/faculty-staff',
    'florida state university schools': 'https://nursing.fsu.edu/faculty-staff',
    'fort myers technical college': 'https://www.fortmyerstech.edu/programs/health-science/',
    'fred k. marchman technical college': 'https://mttc.pasco.k12.fl.us/',
    'fvi school of nursing and technology': 'https://fvi.edu/faculty/',
    'hope college of arts & sciences': 'https://hopecollegefl.edu/faculty/',
    'indian river state college': 'https://irsc.edu/programs/nursing.html',
    'itech glades technical college': 'https://www.itech.edu/',
    'lindsey hopkins technical college': 'https://www.lindseyhopkins.edu/',
    'lively technical center': 'https://www.livelytech.com/',
    'manatee technical college': 'https://www.manateetech.edu/programs/health-science/',
    'marion technical college': 'https://www.mariontc.edu/programs/health-science/',
    'miami dade college': 'https://www.mdc.edu/nursing/faculty-staff.aspx',
    'palm harbor university': 'https://www.pcsb.org/phuhs',
    'pinellas technical college': 'https://www.myptc.edu/programs/health-science/',
    'radford m. locklin technical college': 'https://www.locklintech.com/programs/',
    'santa fe college': 'https://www.sfcollege.edu/directory',
    'st. johns river state college': 'https://www.sjrstate.edu/nursing',
    'tallahassee state college': 'https://www.tsc.fl.edu/academics/academic-divisions/healthcare/',
    'taylor college': 'https://www.taylorcollege.edu/',
    'treasure coast technical college': 'https://www.indianriverschools.org/tctc'
}

def extract_schools_from_text(text: str) -> list:
    """Extracts and deduplicates school names from plain text, state registries, or OCR content."""
    lines = text.split('\n')
    extracted = []
    
    # Exact single tokens to ignore (table headers)
    skip_exact = {'total', 'delivered', 'passed', 'failed', 'rate', '% pass', 'type', 
                  'associates', 'bachelors', 'lic nbr', 'status', 'program name', 
                  'address', 'location', 'st', 'zip', 'phone', 'email addr', 'rn'}

    # Noise keywords/phrases to strip
    skip_phrases = ['national pass rate', 'georgia pass rate', 'total delivered', 'total passed',
                    'board of nursing', 'education program data', 'rev jan',
                    'lic nbr status program name', 'registered nursing (rn) type']

    for line in lines:
        clean = line.strip()
        if not clean or len(clean) < 4:
            continue
        lower = clean.lower()
        if lower in skip_exact or any(term in lower for term in skip_phrases) or re.match(r'^(total|delivered|passed|failed|rate|% pass)\b', lower):
            continue
        # Skip dates (e.g. 8/27/2026)
        if re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', clean):
            continue

        # Handle State Licensing Board format (e.g., Florida CNA training programs)
        # 1. Strip leading license numbers & approval status
        clean = re.sub(r'^(?:\d+\s+)?(?:Approved|Accredited|Probation|Active|Inactive|Closed|Pending|Licensed)\s+', '', clean, flags=re.IGNORECASE).strip()
        
        # 2. Strip trailing email addresses
        clean = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+.*$', '', clean).strip()

        # 3. Strip trailing phone numbers
        clean = re.sub(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}.*$', '', clean).strip()

        # 4. Strip US state abbreviation and 5-digit zip code
        clean = re.sub(r'\b[A-Z]{2}\s+\d{5}(?:-\d{4})?.*$', '', clean).strip()

        # 5. Strip street address if preceded by institution name
        m_addr = re.search(r'\s+(\d{1,5}\s+[A-Za-z0-9#].*)$', clean)
        if m_addr and m_addr.start() > 3:
            clean = clean[:m_addr.start()].strip()

        # 6. Handle Georgia NCLEX format: Remove degree track descriptors (Associates, BSN, Pre-Licensure, etc.)
        clean = re.sub(r'\s+(Associates?|Bachelors?|Pre-Licensure|Masters?|Doctorate|BSN|MSN|DNP|RN)\b.*$', '', clean, flags=re.IGNORECASE).strip()

        # 7. Strip trailing numeric statistics from table columns
        clean = re.sub(r'\s+\d+.*$', '', clean).strip()
        clean = re.sub(r'\s{2,}', ' ', clean).strip(' -,\t')

        if len(clean) >= 4 and not re.match(r'^\d+$', clean) and clean.lower() not in skip_exact:
            extracted.append(clean)

    return deduplicate_school_names(extracted)

def extract_schools_from_pdf(pdf_path: str) -> list:
    """Extracts school names from a PDF document using pypdf."""
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        all_text = []
        for page in reader.pages:
            t = page.extract_text() or ""
            all_text.append(t)
        return extract_schools_from_text("\n".join(all_text))
    except Exception as e:
        print(f"[!] Error reading PDF: {e}")
        return []

def extract_schools_from_excel(excel_path: str) -> list:
    """Extracts school names from Excel (.xlsx/.xls) or CSV spreadsheet."""
    try:
        if excel_path.endswith('.csv'):
            df = pd.read_csv(excel_path)
        else:
            df = pd.read_excel(excel_path)

        # Look for school/institution column
        candidate_cols = [c for c in df.columns if any(k in str(c).lower() for k in ['school', 'institution', 'college', 'university', 'account', 'name'])]
        target_col = candidate_cols[0] if candidate_cols else df.columns[0]
        
        raw_list = df[target_col].dropna().astype(str).tolist()
        return deduplicate_school_names(raw_list)
    except Exception as e:
        print(f"[!] Error reading Excel: {e}")
        return []

def deduplicate_school_names(schools: list) -> list:
    """Deduplicates and cleans school names or direct URLs."""
    seen = set()
    deduped = []
    
    for s in schools:
        clean = s.strip()
        if not clean:
            continue
            
        # If it's a URL, normalize it
        if clean.startswith(('http://', 'https://')):
            clean_url = clean.rstrip('/')
            key = clean_url.lower()
            if key and key not in seen:
                seen.add(key)
                deduped.append(clean)
            continue

        # Clean degree suffixes often appended in state pass-rate documents
        clean = re.sub(r'\s+(Associates?|Bachelors?|Masters?|BSN|MSN|DNP).*$', '', clean, flags=re.IGNORECASE).strip()
        clean = re.sub(r'[\r\n\t]+', ' ', clean).strip()
        
        key = clean.lower()
        if key and key not in seen and len(clean) > 3:
            seen.add(key)
            deduped.append(clean)
            
    return deduped

def query_gemini_for_directory(school_name: str, api_key: str) -> list:
    """Calls Gemini AI via google-genai to determine candidate official nursing faculty directory URLs."""
    candidates = []
    try:
        from google import genai
        use_vertex = api_key.startswith("AQ.")
        client = genai.Client(vertexai=use_vertex, api_key=api_key)
        search_query = f"{school_name} Nursing Faculty directory"
        prompt = f"""You are an elite higher education researcher.

Step 1: First simulate a targeted web search for: "{search_query}"
Step 2: Inspect the top 3 candidate websites/URLs that correspond to this institution.
Step 3: Check whether each page is an actual Nursing Faculty & Staff Directory (listing individual professor, instructor, and leadership names, titles, and contact information), School of Nursing Faculty roster, or College Faculty Directory.
Step 4: Filter out any generic university homepages, program descriptions, admissions, or course catalogs.

Return ONLY the top 1 to 3 direct faculty directory web URLs, one per line. Do not include markdown, numbering, or explanations.
"""
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt
        )
        for line in (response.text or "").split('\n'):
            line = line.strip().strip('"\'`')
            m = re.search(r'https?://[^\s"\'<>]+', line)
            if m:
                u = m.group(0).rstrip('.,;:)')
                if u not in candidates:
                    candidates.append(u)
    except Exception as e:
        pass
    return candidates

KNOWN_DOMAINS = {
    'vcu.edu': 'Virginia Commonwealth University',
    'vanderbilt.edu': 'Vanderbilt University',
    'jhu.edu': 'Johns Hopkins University',
    'etsu.edu': 'East Tennessee State University',
    'abac.edu': 'Abraham Baldwin Agricultural College',
    'asurams.edu': 'Albany State University',
    'albanytech.edu': 'Albany Technical College',
    'andrewcollege.edu': 'Andrew College',
    'athenstech.edu': 'Athens Technical College',
    'atlantatech.edu': 'Atlanta Technical College',
    'augustatech.edu': 'Augusta Technical College',
    'augusta.edu': 'Augusta University',
    'averett.edu': 'Averett University',
    'berry.edu': 'Berry College',
    'brenau.edu': 'Brenau University',
    'bpc.edu': 'Brewton-Parker College',
    'cambridgehealth.edu': 'Cambridge College of Healthcare & Technology',
    'centralgatech.edu': 'Central Georgia Technical College',
    'chamberlain.edu': 'Chamberlain University',
    'chattahoocheetech.edu': 'Chattahoochee Technical College',
    'clayton.edu': 'Clayton State University',
    'coastalpines.edu': 'Coastal Pines Technical College',
    'ccga.edu': 'College of Coastal Georgia',
    'columbusstate.edu': 'Columbus State University',
    'columbustech.edu': 'Columbus Technical College',
    'daltonstate.edu': 'Dalton State College',
    'emory.edu': 'Emory University',
    'fvsu.edu': 'Fort Valley State University',
    'mercer.edu': 'Mercer University',
    'gcsu.edu': 'Georgia College & State University',
    'ggc.edu': 'Georgia Gwinnett College',
    'highlands.edu': 'Georgia Highlands College',
    'gntc.edu': 'Georgia Northwestern Technical College',
    'georgiasouthern.edu': 'Georgia Southern University',
    'gsw.edu': 'Georgia Southwestern State University',
    'gsu.edu': 'Georgia State University',
    'gordonstate.edu': 'Gordon State College',
    'gwinnetttech.edu': 'Gwinnett Technical College',
    'herzing.edu': 'Herzing University',
    'kennesaw.edu': 'Kennesaw State University',
    'lagrange.edu': 'LaGrange College',
    'laniertech.edu': 'Lanier Technical College',
    'mga.edu': 'Middle Georgia State University',
    'northgatech.edu': 'North Georgia Technical College',
    'oftc.edu': 'Oconee Fall Line Technical College',
    'ogeecheetech.edu': 'Ogeechee Technical College',
    'piedmont.edu': 'Piedmont University',
    'reinhardt.edu': 'Reinhardt University',
    'shorter.edu': 'Shorter University',
    'south.edu': 'South College',
    'sgsc.edu': 'South Georgia State College',
    'southuniversity.edu': 'South University',
    'southeasterntech.edu': 'Southeastern Technical College',
    'sctech.edu': 'Southern Crescent Technical College',
    'southernregionaltech.edu': 'Southern Regional Technical College',
    'latech.edu': 'Louisiana Tech University',
    'utf.edu': 'University of True Freedom',
    'tmu.edu': 'Truett McConnell University',
    'ung.edu': 'University of North Georgia',
    'westga.edu': 'University of West Georgia',
    'valdosta.edu': 'Valdosta State University',
    'wesleyancollege.edu': 'Wesleyan College',
    'westgatech.edu': 'West Georgia Technical College',
    'wiregrass.edu': 'Wiregrass Georgia Technical College'
}

def get_school_name_from_url(url: str) -> str:
    """Resolves institutional name from a direct URL domain."""
    try:
        parsed = urlparse(url)
        clean_domain = parsed.netloc.lower().replace("www.", "")
        for k, v in KNOWN_DOMAINS.items():
            if clean_domain == k or clean_domain.endswith("." + k):
                return v
        core = clean_domain.split('.')[0]
        return f"{core.title()} University"
    except Exception:
        return "Educational Institution"

def verify_faculty_directory_url(url: str) -> dict:
    """Pings a candidate URL and verifies HTTP status and faculty directory content indicators."""
    if not url or not url.startswith('http'):
        return {"url": url, "status": "Invalid URL", "valid": False, "score": 0}
        
    try:
        r = requests.get(url, headers=HEADERS, timeout=6, verify=False, allow_redirects=True)
        final_url = re.sub(r'([^:])//+', r'\1/', r.url)
        status = r.status_code
        text_lower = r.text.lower()

        # Detect WAF / Cloudflare bot challenges or Wordfence rate limits
        if (status in [403, 503] and ("challenges.cloudflare.com" in text_lower or "just a moment..." in text_lower or "limited by the site owner" in text_lower or "wordfence" in text_lower or "access denied" in text_lower or status == 403)):
            return {
                "url": final_url,
                "status": f"WAF/Bot Protected (HTTP {status})",
                "valid": False,
                "is_cloudflare": True,
                "score": 85
            }

        # Detect 404 or missing page
        if status in [404, 410, 500, 502, 503] or "page not found" in text_lower or "404 not found" in text_lower:
            return {
                "url": final_url,
                "status": f"HTTP {status} (Not Found)",
                "valid": False,
                "is_cloudflare": False,
                "score": 0
            }

        # Check content markers
        has_dir = bool(re.search(r'\b(faculty|directory|staff|professors?|instructors?|dean|chair|coordinator|personnel|people|profiles)\b', text_lower))
        has_nurs = bool(re.search(r'\b(nurs|rn|bsn|msn|dnp|health professions?|allied health|health sciences?)\b', text_lower))

        score = 0
        if status == 200:
            score += 40
        if has_dir:
            score += 35
        if has_nurs:
            score += 25

        # Check URL path signals
        url_lower = final_url.lower()
        if any(term in url_lower for term in ['faculty', 'directory', 'nursing', 'health', 'profiles', 'personnel', 'staff']):
            score += 20

        is_known = any(v == url or url.rstrip('/') == v.rstrip('/') or v == final_url or final_url.rstrip('/') == v.rstrip('/') for v in KNOWN_VERIFIED.values())
        if is_known and status == 200:
            is_valid = True
            status_label = "Verified Active Directory (HTTP 200)"
        else:
            is_valid = (score >= 50 and status == 200)
            status_label = "Verified Active Directory (HTTP 200)" if is_valid else f"HTTP {status} (Not a directory page)"

        return {
            "url": final_url,
            "status": status_label,
            "valid": is_valid,
            "is_cloudflare": False,
            "score": score
        }
    except Exception as e:
        err_msg = str(e)
        return {
            "url": url,
            "status": f"Connection Timeout / Blocked ({err_msg[:30]})",
            "valid": False,
            "is_cloudflare": False,
            "score": 0
        }

def find_directory_for_school(school: str, gemini_key: str = "") -> dict:
    """Finds and verifies the best nursing faculty directory URL for a given school or URL."""
    clean_school = school.strip()
    is_direct_url = clean_school.startswith(('http://', 'https://'))
    display_school = get_school_name_from_url(clean_school) if is_direct_url else clean_school
    lower_name = display_school.lower()
    candidate_urls = []

    if is_direct_url:
        candidate_urls.append(clean_school)

    # 1. Match known verified directories (exact match first, then longest substring key)
    if lower_name in KNOWN_VERIFIED:
        if KNOWN_VERIFIED[lower_name] not in candidate_urls:
            candidate_urls.append(KNOWN_VERIFIED[lower_name])
    else:
        for k in sorted(KNOWN_VERIFIED.keys(), key=len, reverse=True):
            if k in lower_name:
                if KNOWN_VERIFIED[k] not in candidate_urls:
                    candidate_urls.append(KNOWN_VERIFIED[k])
                break

    # 2. Query Gemini AI for candidates
    if gemini_key and not is_direct_url:
        ai_candidates = query_gemini_for_directory(display_school, gemini_key)
        for u in ai_candidates:
            if u not in candidate_urls:
                candidate_urls.append(u)

    # 3. Add path variations and normalize URLs
    expanded = list(candidate_urls)
    for u in candidate_urls:
        parsed = urlparse(u)
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip('/')
        
        if '/nursing' in path.lower():
            n_idx = path.lower().index('/nursing') + 8
            nursing_prefix = path[:n_idx]
            expanded.append(f"{base}{nursing_prefix}/faculty")
            expanded.append(f"{base}{nursing_prefix}/faculty-staff")
            expanded.append(f"{base}{nursing_prefix}/directory")
            expanded.append(f"{base}{nursing_prefix}")
        
        if path.endswith('/directory'):
            parent = path[:-10]
            expanded.append(f"{base}{parent}/faculty")
            expanded.append(f"{base}{parent}/faculty-staff")
        elif path.endswith('/faculty'):
            parent = path[:-8]
            expanded.append(f"{base}{parent}/directory")
            expanded.append(f"{base}{parent}/faculty-staff")

        if 'nursing.' in parsed.netloc.lower():
            expanded.append(f"{base}/faculty")
            expanded.append(f"{base}/directory")
            expanded.append(f"{base}/faculty-staff")

    clean_candidates = []
    for cand in expanded:
        p = urlparse(cand)
        clean_path = re.sub(r'/{2,}', '/', p.path)
        norm_url = f"{p.scheme}://{p.netloc}{clean_path}"
        if p.query:
            norm_url += f"?{p.query}"
        if norm_url not in clean_candidates:
            clean_candidates.append(norm_url)
    candidate_urls = clean_candidates

    # 4. Fallback if no candidate
    if not candidate_urls:
        core = re.sub(r'[^a-z0-9]', '', lower_name)
        candidate_urls.append(f"https://www.{core}.edu/faculty/")
        candidate_urls.append(f"https://www.{core}.edu/directory/")

    # 5. Verify candidate URLs
    best_result = None
    best_score = -1
    for cand in candidate_urls:
        ver = verify_faculty_directory_url(cand)
        cand_lower = cand.lower()
        bonus = 0
        if any(term in cand_lower for term in ['/faculty', '/faculty-staff', '/faculty-and-staff', '/directory', '/people', '/personnel']):
            bonus += 20
        total_eval_score = ver.get("score", 0) + bonus

        if ver.get("valid"):
            if total_eval_score > best_score:
                best_score = total_eval_score
                best_result = ver
        elif not best_result or (best_score == -1 and total_eval_score > best_score):
            best_result = ver
            best_score = total_eval_score

    if not best_result:
        best_result = {"url": candidate_urls[0] if candidate_urls else "", "status": "Unverified", "valid": False}

    return {
        "school": display_school,
        "directory_url": best_result["url"],
        "status": best_result["status"],
        "is_ready_for_extractor": best_result.get("valid", False)
    }

def process_school_batch(schools: list, gemini_key: str = "", max_workers: int = 5) -> list:
    """Processes a batch of schools concurrently with input and output deduplication."""
    # Deduplicate input list
    schools = deduplicate_school_names(schools)
    results = []
    print(f"\n[*] Searching & verifying directories for {len(schools)} unique entries with {max_workers} concurrent workers...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_school = {
            executor.submit(find_directory_for_school, sch, gemini_key): sch
            for sch in schools
        }
        for future in as_completed(future_to_school):
            res = future.result()
            results.append(res)
            icon = "✓" if res["is_ready_for_extractor"] else "⚠️"
            print(f"  {icon} [{res['status'][:24]}] {res['school'][:32]} -> {res['directory_url']}")

    # Deduplicate results by normalized directory URL so each directory appears once
    deduped_results = []
    seen_urls = set()
    for r in results:
        norm_url = r["directory_url"].rstrip('/').lower()
        if norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)
        deduped_results.append(r)

    deduped_results.sort(key=lambda x: x["school"])
    return deduped_results

def save_and_export_results(results: list, output_excel: str = "Schools_Verified_Directories.xlsx"):
    """Saves ONLY verified active directories to Excel, CSV, and clean text list."""
    if os.getenv("VERCEL") or not os.access(BACKEND_DIR, os.W_OK):
        exports_dir = os.path.join(tempfile.gettempdir(), "exports")
    else:
        exports_dir = os.path.join(BACKEND_DIR, "exports")
    os.makedirs(exports_dir, exist_ok=True)
    
    verified_results = [
        r for r in results 
        if r.get("is_ready_for_extractor") and ("200" in r.get("status", "") or "Verified Active" in r.get("status", "")) and "waf" not in r.get("status", "").lower()
    ]
    if not verified_results:
        print("[!] Notice: No directories verified active (HTTP 200).")
        return

    clean_rows = [
        {
            "School Name": r["school"],
            "Nursing Faculty Directory URL": r["directory_url"],
            "Verification Status": r["status"]
        }
        for r in verified_results
    ]

    excel_path = os.path.join(exports_dir, output_excel)
    csv_filename = output_excel.replace(".xlsx", ".csv") if output_excel.endswith(".xlsx") else f"{output_excel}.csv"
    csv_path = os.path.join(exports_dir, csv_filename)
    
    df = pd.DataFrame(clean_rows)
    df.to_excel(excel_path, index=False)
    df.to_csv(csv_path, index=False)

    txt_filename = output_excel.replace(".xlsx", "_urls.txt") if output_excel.endswith(".xlsx") else f"{output_excel}_urls.txt"
    txt_path = os.path.join(exports_dir, txt_filename)
    
    with open(txt_path, "w", encoding="utf-8") as f:
        for r in verified_results:
            if r.get("directory_url"):
                f.write(r["directory_url"] + "\n")

    print("\n" + "=" * 80)
    print("🎉 Verified Directory Discovery Complete!")
    print("=" * 80)
    print(f"📊 Verified Directories Count: {len(verified_results)}")
    print(f"📁 Excel Report:     {os.path.abspath(excel_path)}")
    print(f"📄 CSV Report:       {os.path.abspath(csv_path)}")
    print(f"📋 Verified URLs:    {os.path.abspath(txt_path)}")
    print("=" * 80 + "\n")
    return excel_path, txt_path

def main():
    parser = argparse.ArgumentParser(description="Automated Nursing Faculty Directory Finder & Validator")
    parser.add_argument("--file", "-f", help="Path to input PDF, Excel (.xlsx/.xls), or CSV file")
    parser.add_argument("--schools", "-s", nargs="+", help="One or more school names")
    parser.add_argument("--output", "-o", default="GA_Schools_Verified_Directories.xlsx", help="Output Excel filename")
    parser.add_argument("--concurrency", "-c", type=int, default=5, help="Number of concurrent workers (default: 5)")
    args = parser.parse_args()

    schools = []
    if args.file:
        fpath = args.file
        if not os.path.exists(fpath):
            print(f"❌ File not found: {fpath}")
            sys.exit(1)
        ext = os.path.splitext(fpath)[1].lower()
        if ext == ".pdf":
            print(f"[*] Extracting schools from PDF: {fpath}...")
            schools = extract_schools_from_pdf(fpath)
        elif ext in [".xlsx", ".xls", ".csv"]:
            print(f"[*] Extracting schools from Spreadsheet: {fpath}...")
            schools = extract_schools_from_excel(fpath)
        else:
            with open(fpath, "r", encoding="utf-8") as f:
                schools = extract_schools_from_text(f.read())
    elif args.schools:
        schools = deduplicate_school_names(args.schools)
    else:
        print("\n" + "=" * 70)
        print("🎓 ContactFinder - Automated Nursing Directory Finder")
        print("=" * 70)
        user_in = input("\nEnter school names (comma-separated) or path to PDF/Excel file: ").strip()
        if os.path.exists(user_in):
            ext = os.path.splitext(user_in)[1].lower()
            if ext == ".pdf":
                schools = extract_schools_from_pdf(user_in)
            elif ext in [".xlsx", ".xls", ".csv"]:
                schools = extract_schools_from_excel(user_in)
            else:
                with open(user_in, "r", encoding="utf-8") as f:
                    schools = extract_schools_from_text(f.read())
        elif user_in:
            schools = deduplicate_school_names(user_in.split(","))

    if not schools:
        print("❌ No school names provided or extracted.")
        sys.exit(1)

    print(f"[✓] Loaded {len(schools)} unique schools to search.")
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()

    results = process_school_batch(schools, gemini_key=gemini_key, max_workers=args.concurrency)
    save_and_export_results(results, output_excel=args.output)

if __name__ == "__main__":
    main()
