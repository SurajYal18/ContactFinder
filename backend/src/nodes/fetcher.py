import re
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
from src.state import ScraperState

# Standard browser request headers to avoid blocking
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# Cleans raw HTML text into readable lines preserving emails and phone links
def sanitize_raw_html(html: str) -> str:
    # Strip script blocks
    clean = re.sub(r'<script.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    # Strip style blocks
    clean = re.sub(r'<style.*?</style>', ' ', clean, flags=re.DOTALL | re.IGNORECASE)
    # Strip SVG elements
    clean = re.sub(r'<svg.*?</svg>', ' ', clean, flags=re.DOTALL | re.IGNORECASE)
    # Strip HTML comments
    clean = re.sub(r'<!--.*?-->', ' ', clean, flags=re.DOTALL)
    # Decode obfuscated data-name and data-domain email attributes
    clean = re.sub(r'<a[^>]*data-name=[\'"]([^\'"]+)[\'"][^>]*data-domain=[\'"]([^\'"]+)[\'"][^>]*>.*?</a>', r' Email: \1@\2 ', clean, flags=re.IGNORECASE | re.DOTALL)
    # Preserve mailto links as explicit text
    clean = re.sub(r'<a[^>]+href=[\'"]mailto:([^\'"]+)[\'"][^>]*>.*?</a>', r' Email: \1 ', clean, flags=re.IGNORECASE)
    # Preserve tel links as explicit text
    clean = re.sub(r'<a[^>]+href=[\'"]tel:([^\'"]+)[\'"][^>]*>.*?</a>', r' Phone: \1 ', clean, flags=re.IGNORECASE)
    # Convert block line breaks and headings into newlines
    clean = re.sub(r'<(?:br|p|div|tr|li|h[1-6])[^>]*>', '\n', clean, flags=re.IGNORECASE)
    # Strip all remaining HTML tags
    clean = re.sub(r'<[^>]+>', ' ', clean)
    # Clean up empty lines and excess spaces
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in clean.split('\n')]
    return '\n'.join([l for l in lines if l])

# Fetches web content handling standard GET or form-based search portals
def fetch_page_content(url: str) -> str:
    # First attempt standard GET request
    res = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
    if res.status_code == 403 or "challenges.cloudflare.com" in res.text or "Just a moment..." in res.text:
        raise RuntimeError("Access blocked: This website is protected by Cloudflare bot protection (HTTP 403 Forbidden).")
    res.raise_for_status()
    html = res.text

    # Check if page is an interactive form search portal like Augusta University
    if "search.php" in html and ("Filter by Unit" in html or 'name="college"' in html):
        search_url = urljoin(url, "search.php")
        # Submit POST payload specifically requesting nursing unit faculty
        post_data = {"college": "4", "first": "", "last": ""}
        post_res = requests.post(search_url, headers=BROWSER_HEADERS, data=post_data, timeout=20)
        if post_res.status_code == 200 and len(post_res.text) > len(html):
            return post_res.text

    # Check if page is a dynamic AJAX provider directory (e.g., GNTC)
    if "pf_provider_list" in html or "wp-json/custom/v1/providers/list" in html:
        try:
            api_url = urljoin(url, "/wp-json/custom/v1/providers/list/")
            opt_matches = re.findall(r'<option[^>]+value=[\'"](\d+)[\'"][^>]*>([^<]+)</option>', html, re.I)
            dept_ids = [opt[0] for opt in opt_matches if re.search(r'nurs', opt[1], re.I)]
            if not dept_ids:
                dept_ids = ["0"]

            combined_html = html
            for dept_id in dept_ids:
                post_data = {
                    "filter_specialty": dept_id,
                    "filter_practice": "",
                    "filter_ft": "1",
                    "filter_location": "0",
                    "filter_query": ""
                }
                api_res = requests.post(api_url, headers=BROWSER_HEADERS, data=post_data, timeout=20)
                if api_res.status_code == 200:
                    api_json = api_res.json()
                    res_html = api_json.get("html", "")
                    if res_html:
                        decoded = re.sub(
                            r'<a[^>]*data-name=[\'"]([^\'"]+)[\'"][^>]*data-domain=[\'"]([^\'"]+)[\'"][^>]*>.*?</a>',
                            r' Email: \1@\2 ',
                            res_html,
                            flags=re.I | re.DOTALL
                        )
                        combined_html += "\n\n" + decoded
            return combined_html
        except Exception:
            pass

    # Check if page is an Angular/AJAX JSON feed directory (e.g., LaGrange College)
    json_feed_match = re.search(r'(?:var\s+jsonFeed|jsonFeed)\s*=\s*[\'"]([^\'"]+\.json[^\'"]*)[\'"]', html, re.I)
    if not json_feed_match:
        json_feed_match = re.search(r'[\'"]([^\'"]+directory[^\'"]*\.json[^\'"]*)[\'"]', html, re.I)

    if json_feed_match:
        try:
            feed_rel = json_feed_match.group(1).replace("amp;", "")
            feed_url = urljoin(url, feed_rel)
            feed_res = requests.get(feed_url, headers=BROWSER_HEADERS, timeout=20)
            if feed_res.status_code == 200:
                feed_data = feed_res.json()
                items = []
                if isinstance(feed_data, dict):
                    for k in ["faculty", "directory", "staff", "people", "members", "data"]:
                        if k in feed_data and isinstance(feed_data[k], list):
                            items = feed_data[k]
                            break
                    if not items and any(isinstance(v, dict) for v in feed_data.values()):
                        items = list(feed_data.values())
                elif isinstance(feed_data, list):
                    items = feed_data

                if items:
                    cards = []
                    for it in items:
                        if isinstance(it, dict):
                            fn = it.get("firstName") or it.get("first_name") or it.get("name", "")
                            ln = it.get("lastName") or it.get("last_name") or ""
                            full_name = f"{fn} {ln}".strip() if ln else fn
                            title = it.get("title") or it.get("position") or it.get("role", "")
                            dept = it.get("departments") or it.get("deptDisplay") or it.get("department", "")
                            email = it.get("email") or it.get("mail", "")
                            phone = it.get("phone") or it.get("telephone", "")
                            office = it.get("office") or it.get("location") or it.get("campus", "")
                            card = f"""
                            <div class="faculty-card">
                                <h3>{full_name}</h3>
                                <p>Title: {title}</p>
                                <p>Department: {dept}</p>
                                <p>Email: <a href="mailto:{email}">{email}</a></p>
                                <p>Phone: {phone}</p>
                                <p>Campus: {office}</p>
                            </div>
                            """
                            cards.append(card)
                    if cards:
                        return html + "\n\n<!-- LOADED DYNAMIC JSON FEED -->\n" + "\n".join(cards)
        except Exception:
            pass

    return html

# Resolves the university or institutional name from the domain or page metadata
def get_university_name(domain: str, html: str = "") -> str:
    known_unis = {
        "lagrange.edu": "LaGrange College",
        "gntc.edu": "Georgia Northwestern Technical College",
        "asurams.edu": "Albany State University",
        "georgiasouthern.edu": "Georgia Southern University",
        "augusta.edu": "Augusta University",
        "uga.edu": "University of Georgia",
        "gsu.edu": "Georgia State University",
        "kennesaw.edu": "Kennesaw State University",
        "gatech.edu": "Georgia Institute of Technology",
        "columbusstate.edu": "Columbus State University",
        "ung.edu": "University of North Georgia",
        "valdosta.edu": "Valdosta State University",
        "westga.edu": "University of West Georgia",
        "gcsu.edu": "Georgia College & State University",
        "emory.edu": "Emory University",
        "mercer.edu": "Mercer University"
    }
    clean_domain = domain.replace("www.", "").lower()
    for k, v in known_unis.items():
        if k in clean_domain:
            return v

    # Extract university name from title tag if available
    if html:
        m = re.search(r'<title[^>]*>(.*?)</title>', html, flags=re.I | re.DOTALL)
        if m:
            title_text = re.sub(r'\s+', ' ', m.group(1)).strip()
            parts = [p.strip() for p in re.split(r'[-|–—•]', title_text) if p.strip()]
            for p in reversed(parts):
                if any(w in p.lower() for w in ["university", "college", "institute"]):
                    return p

    # Fallback to domain core title
    core = clean_domain.split('.')[0]
    return f"{core.title()} University"

# Terms indicating non-person navigation links
EXCLUDE_URL_TERMS = {
    "cost", "aid", "tuition", "admission", "admissions", "apply", "giving", "alumni", "athletic",
    "athletics", "news", "event", "events", "calendar", "privacy", "term", "terms", "accessibility",
    "map", "maps", "catalog", "catalogs", "degree", "degrees", "program", "programs", "curriculum",
    "course", "courses", "resource", "resources", "international", "board", "records", "housing",
    "dining", "bookstore", "jobs", "career", "careers", "portal", "login", "search", "student",
    "students", "undergraduate", "graduate", "accreditation", "advisory", "handbook", "exam",
    "department", "departments", "college", "colleges", "schedule"
}

# Profile path indicators across university websites
PROFILE_PATH_MARKERS = [
    "/profile/", "/profiles/", "/faculty/", "/faculty-staff/", "/facultystaff/",
    "/people/", "/person/", "/bio/", "/bios/", "/directory/profile", "/employee/"
]

# Checks if a hyperlink points to an individual faculty profile or bio page
def is_faculty_profile_link(full_url: str, text: str, base_domain: str, parent_classes: str = "") -> bool:
    parsed = urlparse(full_url)
    root_domain = ".".join(base_domain.split(".")[-2:]) if base_domain.count(".") >= 2 else base_domain
    if root_domain not in parsed.netloc.lower():
        return False
    path_lower = parsed.path.lower()
    text_clean = text.strip()
    text_words = [w for w in text_clean.split() if w]
    path_segments = [s for s in path_lower.strip("/").split("/") if s]
    if not path_segments:
        return False
    for seg in path_segments:
        clean_seg = re.sub(r'\.(?:php|html|htm|aspx)$', '', seg)
        if any(term in clean_seg for term in EXCLUDE_URL_TERMS):
            return False
    if any(term in text_clean.lower() for term in EXCLUDE_URL_TERMS):
        return False
    has_profile_pattern = any(marker in path_lower for marker in PROFILE_PATH_MARKERS)
    last_seg = path_segments[-1]
    is_not_root_dir = last_seg not in ["profile", "profiles", "faculty", "people", "staff", "directory", "bio"]
    if has_profile_pattern and is_not_root_dir:
        return True
    if 2 <= len(text_words) <= 4:
        first_word = text_words[0].rstrip(".").lower()
        has_honorific = first_word in ["dr", "prof", "mr", "ms", "mrs"]
        is_title_case_name = all(w[0].isupper() for w in text_words if w.isalpha())
        if (has_honorific or is_title_case_name) and len(path_segments) >= 2:
            return True
    if any(c in parent_classes.lower() for c in ["card", "faculty", "staff", "profile", "bio", "member"]):
        if 2 <= len(text_words) <= 4 and all(w[0].isupper() for w in text_words if w.isalpha()):
            return True
    return False

# Crawls candidate individual faculty profile sub-pages concurrently to extract email, phone, and credentials
def crawl_faculty_profiles(main_url: str, html: str, max_profiles: int = 40) -> str:
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(main_url).netloc.lower()
    seen_urls = set()
    candidate_links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        full_url = urljoin(main_url, href).split("?")[0].split("#")[0]
        text = a.get_text(" ", strip=True)
        parent = a.find_parent(["div", "li", "td", "article", "section"])
        parent_classes = (" ".join(parent.get("class", [])) + " " + (parent.get("id") or "")) if parent else ""
        if full_url in seen_urls:
            continue
        if is_faculty_profile_link(full_url, text, base_domain, parent_classes):
            seen_urls.add(full_url)
            candidate_links.append((full_url, text))
    if not candidate_links:
        return ""
    candidate_links = candidate_links[:max_profiles]
    results = []
    def fetch_one(entry):
        p_url, p_name = entry
        try:
            r = requests.get(p_url, headers=BROWSER_HEADERS, timeout=8)
            if r.status_code == 200:
                txt = sanitize_raw_html(r.text)
                return (p_url, p_name, txt)
        except Exception:
            pass
        return None
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_one, link) for link in candidate_links]
        for f in as_completed(futures):
            res = f.result()
            if res:
                results.append(res)
    profile_sections = []
    for p_url, p_name, p_text in results:
        profile_sections.append(f"=== FACULTY PROFILE: {p_name} ({p_url}) ===\n{p_text[:2000]}")
    return "\n\n".join(profile_sections)

# LangGraph node function for fetching and sanitizing target directory URL
def fetch_node(state: ScraperState) -> ScraperState:
    url = state.get("url", "").strip()
    if not url:
        return {"error": "No URL provided to fetch_node", "status": "Failed: missing URL"}

    domain = urlparse(url).netloc.lower()
    try:
        raw_html = fetch_page_content(url)
        university = get_university_name(domain, raw_html)

        # If page contains a massive multi-department table, isolate nursing rows
        tr_blocks = re.findall(r'<tr[^>]*>.*?</tr>', raw_html, flags=re.DOTALL | re.IGNORECASE)
        processed_html = raw_html
        if len(tr_blocks) > 100:
            header_tr = tr_blocks[0] if tr_blocks else ""
            target_trs = [tr for tr in tr_blocks[1:] if "nurs" in tr.lower()]
            # Retain contact info or heading block before the table
            m = re.search(r'Contact Info.*?(?:Button HTML|<table)', raw_html, flags=re.DOTALL | re.IGNORECASE)
            top_contact = m.group(0) if m else ""
            processed_html = top_contact + "\n<table>\n" + header_tr + "\n" + "\n".join(target_trs) + "\n</table>"

        # Sanitize HTML into clean readable text
        clean_text = sanitize_raw_html(processed_html)

        # Check if page has few emails and contains faculty profile links
        email_matches = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', clean_text)
        if len(email_matches) < 10:
            profiles_text = crawl_faculty_profiles(url, raw_html)
            if profiles_text:
                clean_text += "\n\n=== INDIVIDUAL FACULTY PROFILE BIO PAGES ===\n\n" + profiles_text

        return {
            "raw_html": raw_html,
            "clean_text": clean_text,
            "domain": domain,
            "university": university,
            "status": f"Fetched {len(raw_html):,} bytes ({len(clean_text):,} clean chars) from {university} ({domain})",
            "error": None
        }
    except Exception as e:
        return {
            "error": f"Failed to fetch {url}: {str(e)}",
            "status": "Fetch failed"
        }
