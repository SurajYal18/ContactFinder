import io
import re
import html
import urllib.parse
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import urllib3
try:
    import pypdf
except ImportError:
    pypdf = None
from bs4 import BeautifulSoup
from src.state import ScraperState

# Suppress insecure request warnings for colleges with expired or self-signed SSL certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Standard browser request headers to avoid blocking
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# Decodes Cloudflare XOR obfuscated email strings (data-cfemail)
def decode_cloudflare_email(cfemail: str) -> str:
    try:
        k = int(cfemail[:2], 16)
        return "".join([chr(int(cfemail[i:i+2], 16) ^ k) for i in range(2, len(cfemail), 2)])
    except Exception:
        return ""

# Decodes Caesar cipher shifted email strings (TerminalFour emailob, data-tel, or shifted ASCII)
def decode_caesar_shifted_email(raw_str: str, key: str = "", domain_hint: str = "") -> str:
    if not raw_str:
        return ""
    # Unescape HTML entities (e.g. &gt;, &lt;, &commat;, &#64;, etc.)
    raw = html.unescape(str(raw_str)).strip()
    
    # Check if already a valid email
    if "@" in raw and re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', raw):
        return raw

    # Handle unescaped newline that came from \n in source
    if "\n" in raw:
        raw = raw.replace("\n", "\\n")

    # If key/phone number is provided (e.g. TerminalFour emailob data-tel)
    digits = [c for c in key if c.isdigit()]
    if digits:
        first_val = int(digits[0])
        last_val = int(digits[-1])
        shift = last_val + 1 - first_val
        cand = "".join(chr(ord(c) + shift) for c in raw)
        if domain_hint:
            cand = cand.replace("example.edu", domain_hint)
        if "@" in cand and re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', cand):
            return cand

    # Brute-force Caesar shift from -15 to +15
    for s in range(-15, 16):
        try:
            cand = "".join(chr(ord(c) + s) for c in raw)
            if domain_hint:
                cand = cand.replace("example.edu", domain_hint)
            if "@" in cand:
                parts = cand.split("@")
                if len(parts) == 2 and parts[0] and "." in parts[1]:
                    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', cand):
                        return cand
        except Exception:
            continue

    return raw

# Deobfuscates emails in raw HTML across various CMS obfuscation schemes
def deobfuscate_emails_in_html(html_str: str, domain_hint: str = "") -> str:
    if not html_str:
        return ""

    # 1. Decode Cloudflare data-cfemail
    def replace_cf_email(match):
        cf = match.group(1)
        dec = decode_cloudflare_email(cf)
        return f' Email: {dec} ' if dec else match.group(0)
    
    cleaned = re.sub(r'data-cfemail=[\'"]([a-f0-9]+)[\'"]', replace_cf_email, html_str, flags=re.IGNORECASE)
    cleaned = re.sub(r'/cdn-cgi/l/email-protection#([a-f0-9]+)', lambda m: f'mailto:{decode_cloudflare_email(m.group(1))}', cleaned, flags=re.IGNORECASE)

    # 2. Decode TerminalFour / CMS Caesar shift emails (class="emailob" or data-tel="..." or data-phone="...")
    def replace_emailob_match(match):
        attrs = match.group(1)
        body = match.group(2)
        m_tel = re.search(r'data-(?:tel|phone)=[\'"]([^\'"]+)[\'"]', attrs, re.IGNORECASE)
        tel_val = m_tel.group(1) if m_tel else ""
        dec = decode_caesar_shifted_email(body, tel_val, domain_hint)
        if "@" in dec:
            return f'<a href="mailto:{dec}"> Email: {dec} </a>'
        return match.group(0)

    cleaned = re.sub(
        r'<a([^>]*?(?:class=[\'"][^\'"]*emailob[^\'"]*[\'"]|data-(?:tel|phone)=[\'"][^\'"]+[\'"])[^>]*)>(.*?)</a>',
        replace_emailob_match,
        cleaned,
        flags=re.DOTALL | re.IGNORECASE
    )

    # 3. Decode data-email and data-mailto attributes on buttons/spans/icons
    cleaned = re.sub(r'data-(?:email|mailto)=[\'"]([^\'"]+@[\w\.-]+\.\w+)[\'"]', r' Email: \1 ', cleaned, flags=re.IGNORECASE)

    # 4. Decode JavaScript substitution cipher emails (used by UNG and CMS directory platforms)
    def decode_js_cipher_email(match):
        coded = match.group(1)
        key = match.group(2)
        shift = len(coded)
        link = ""
        for i in range(len(coded)):
            char = coded[i]
            if char not in key:
                link += char
            else:
                idx = (key.index(char) - shift + len(key)) % len(key)
                link += key[idx]
        if "@" in link:
            return f" Email: {link} "
        return ""

    cleaned = re.sub(
        r'<script(?:(?!<script).)*?coded\s*=\s*"([^"]+)"\s*key\s*=\s*"([^"]+)".*?</script>',
        decode_js_cipher_email,
        cleaned,
        flags=re.DOTALL | re.IGNORECASE
    )

    # 5. Decode obfuscated data-name and data-domain email attributes
    cleaned = re.sub(r'<a[^>]*data-name=[\'"]([^\'"]+)[\'"][^>]*data-domain=[\'"]([^\'"]+)[\'"][^>]*>.*?</a>', r' Email: \1@\2 ', cleaned, flags=re.IGNORECASE | re.DOTALL)

    # 6. Decode text obfuscations like user [at] domain [dot] edu
    cleaned = re.sub(r'([a-zA-Z0-9._%+-]+)\s*\[at\]\s*([a-zA-Z0-9.-]+)\s*\[dot\]\s*([a-zA-Z]{2,})', r' Email: \1@\2.\3 ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'([a-zA-Z0-9._%+-]+)\s*\(at\)\s*([a-zA-Z0-9.-]+)\s*\(dot\)\s*([a-zA-Z]{2,})', r' Email: \1@\2.\3 ', cleaned, flags=re.IGNORECASE)

    return cleaned

# Cleans raw HTML text into readable lines preserving emails and phone links
def sanitize_raw_html(html_str: str, domain_hint: str = "") -> str:
    # First, run comprehensive DOM and text deobfuscation
    clean = deobfuscate_emails_in_html(html_str, domain_hint)

    # Strip remaining script blocks
    clean = re.sub(r'<script.*?</script>', ' ', clean, flags=re.DOTALL | re.IGNORECASE)
    # Strip style blocks
    clean = re.sub(r'<style.*?</style>', ' ', clean, flags=re.DOTALL | re.IGNORECASE)
    # Strip SVG elements
    clean = re.sub(r'<svg.*?</svg>', ' ', clean, flags=re.DOTALL | re.IGNORECASE)
    # Strip HTML comments
    clean = re.sub(r'<!--.*?-->', ' ', clean, flags=re.DOTALL)
    # Preserve mailto links as explicit text (including percent-encoded or unescaped mailto links)
    def replace_mailto(match):
        raw_target = match.group(1).split("?")[0].strip()
        unescaped = html.unescape(urllib.parse.unquote(raw_target))
        return f" Email: {unescaped} "
    clean = re.sub(r'<a[^>]+href=[\'"]mailto:([^\'"]+)[\'"][^>]*>.*?</a>', replace_mailto, clean, flags=re.IGNORECASE)
    # Preserve tel links as explicit text
    clean = re.sub(r'<a[^>]+href=[\'"]tel:([^\'"]+)[\'"][^>]*>.*?</a>', r' Phone: \1 ', clean, flags=re.IGNORECASE)
    # Convert block line breaks and headings into newlines
    clean = re.sub(r'<(?:br|p|div|tr|li|h[1-6])[^>]*>', '\n', clean, flags=re.IGNORECASE)
    # Strip all remaining HTML tags
    clean = re.sub(r'<[^>]+>', ' ', clean)
    # Clean up empty lines and excess spaces
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in clean.split('\n')]
    return '\n'.join([l for l in lines if l])

# Automatically submits department filter forms when department selection does not change the URL (e.g. Southern Crescent Tech)
def auto_submit_department_form(url: str, html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for form in soup.find_all("form"):
        selects = form.find_all("select")
        dept_select = None
        target_option_val = None

        for sel in selects:
            for opt in sel.find_all("option"):
                opt_txt = opt.get_text(" ", strip=True).lower()
                val = opt.get("value", "")
                if re.search(r'\b(?:nurs|allied health|health sci)', opt_txt) and val:
                    dept_select = sel.get("name")
                    target_option_val = val
                    break
            if dept_select:
                break

        if dept_select and target_option_val:
            payload = {}
            for inp in form.find_all(["input", "select"]):
                name = inp.get("name")
                if not name:
                    continue
                if name == dept_select:
                    payload[name] = target_option_val
                elif inp.name == "input":
                    val = inp.get("value", "")
                    inp_type = (inp.get("type") or "text").lower()
                    if inp_type in ["hidden", "text", "submit"]:
                        payload[name] = val

            action = form.get("action") or url
            post_url = urljoin(url, action)
            method = (form.get("method") or "POST").upper()
            try:
                if method == "POST":
                    resp = requests.post(post_url, headers=BROWSER_HEADERS, data=payload, timeout=15, verify=False)
                else:
                    resp = requests.get(post_url, headers=BROWSER_HEADERS, params=payload, timeout=15, verify=False)
                if resp.status_code == 200 and len(resp.text) > len(html) * 0.4:
                    return resp.text
            except Exception:
                pass
    return html

# Discovers pagination links on the directory page (path-based /page/N/, query-based ?page=N, rel=next, and page ranges)
def discover_pagination_urls(base_url: str, html: str, max_pages: int = 20) -> list:
    soup = BeautifulSoup(html, "html.parser")
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc.lower()

    seen = {base_url.split("#")[0].rstrip("/")}
    pagination_links = []

    path_page_nums = []
    path_pattern_template = None

    query_page_nums = []
    query_pattern_template = None

    # 1. Scan all links on the page for pagination patterns
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        full_url = urljoin(base_url, href).split("#")[0]
        parsed_target = urlparse(full_url)
        if parsed_target.netloc.lower() != base_domain:
            continue

        clean_target = full_url.rstrip("/")

        # A) Check path-based pagination e.g. /page/2/ or /p/2/
        m_path = re.search(r'/(?:page|paged?|p)/(\d+)/?$', parsed_target.path, re.I)
        if m_path:
            p_num = int(m_path.group(1))
            path_page_nums.append(p_num)
            if not path_pattern_template:
                new_path = parsed_target.path[:m_path.start(1)] + "{}" + parsed_target.path[m_path.end(1):]
                path_pattern_template = parsed_target._replace(path=new_path).geturl()
            continue

        # B) Check query-based pagination e.g. ?page=2, ?p=2, ?pg=2, ?paged=2
        m_query = re.search(r'([?&](?:page|p|pg|paged|page_number|start)=)(\d+)', full_url, re.I)
        if m_query:
            p_num = int(m_query.group(2))
            query_page_nums.append(p_num)
            if not query_pattern_template:
                prefix = full_url[:m_query.start(2)]
                suffix = full_url[m_query.end(2):]
                query_pattern_template = f"{prefix}{{}}{suffix}"
            continue

        # C) Check standard rel="next" or class/text indicators
        classes = " ".join(a.get("class", [])).lower()
        rel = (a.get("rel") or [""])[0].lower() if isinstance(a.get("rel"), list) else str(a.get("rel") or "").lower()
        a_text = a.get_text(" ", strip=True).lower()

        is_page_rel = rel in ["next", "next page"]
        is_page_class = any(c in classes for c in ["next", "page-next", "pagination-next"])
        is_page_text = a_text in ["next", "next page", "›", "»", "❯", ">", "next >", "next »"]

        if (is_page_rel or is_page_class or is_page_text) and clean_target not in seen:
            seen.add(clean_target)
            pagination_links.append(full_url)

    # 2. Check for "Page 1 of N" or "Page X of Y" in body text
    text_max_pages = None
    m_page_text = re.search(r'\bpage\s+\d+\s+of\s+(\d+)\b', html, re.I)
    if m_page_text:
        text_max_pages = int(m_page_text.group(1))

    # 3. If path-based pagination template found, generate the complete numeric range
    if path_pattern_template and path_page_nums:
        max_seen = max(path_page_nums)
        if text_max_pages and text_max_pages > max_seen:
            max_seen = text_max_pages
        effective_max = min(max_seen, max_pages)
        for p in range(2, effective_max + 1):
            gen_url = path_pattern_template.format(p)
            clean_gen = gen_url.rstrip("/")
            if clean_gen not in seen:
                seen.add(clean_gen)
                pagination_links.append(gen_url)

    # 4. If query-based pagination template found, generate the complete numeric range
    if query_pattern_template and query_page_nums:
        max_seen = max(query_page_nums)
        if text_max_pages and text_max_pages > max_seen:
            max_seen = text_max_pages
        effective_max = min(max_seen, max_pages)
        for p in range(2, effective_max + 1):
            gen_url = query_pattern_template.format(p)
            clean_gen = gen_url.rstrip("/")
            if clean_gen not in seen:
                seen.add(clean_gen)
                pagination_links.append(gen_url)

    return pagination_links

# Crawls pagination pages and combines their HTML in sequence
def crawl_pagination_pages(base_url: str, initial_html: str, page_urls: list) -> str:
    if not page_urls:
        return initial_html

    print(f"[*] Crawling {len(page_urls)} pagination pages for {base_url}...", flush=True)
    combined_pages = {}

    def fetch_p(p_url):
        try:
            r = requests.get(p_url, headers=BROWSER_HEADERS, timeout=12, verify=False)
            if r.status_code == 200 and len(r.text) > 1000:
                return (p_url, r.text)
        except Exception:
            pass
        return (p_url, None)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_p, u) for u in page_urls]
        for f in as_completed(futures):
            p_url, p_html = f.result()
            if p_html:
                combined_pages[p_url] = p_html

    # Append pages in the original discovered sequence order
    combined_html = [initial_html]
    for u in page_urls:
        if u in combined_pages:
            combined_html.append(f"\n<!-- NEXT DIRECTORY PAGE: {u} -->\n{combined_pages[u]}")

    print(f"[+] Successfully merged {len(combined_pages)} additional directory pages.", flush=True)
    return "\n\n".join(combined_html)

# Fetches web content handling standard GET, PDF documents, or form-based search portals
def fetch_page_content(url: str) -> str:
    clean_url = url.strip().strip('"\'[]<>').strip()
    
    # First attempt standard GET request with SSL verification fallback
    try:
        res = requests.get(clean_url, headers=BROWSER_HEADERS, timeout=20)
    except requests.exceptions.SSLError:
        # Fallback for schools with expired / self-signed SSL certs (e.g. Emory)
        res = requests.get(clean_url, headers=BROWSER_HEADERS, timeout=20, verify=False)

    if res.status_code == 403 or "challenges.cloudflare.com" in res.text or "Just a moment..." in res.text:
        raise RuntimeError("Access blocked: This website is protected by Cloudflare bot protection (HTTP 403 Forbidden).")
    res.raise_for_status()

    # Check if document is a PDF
    content_type = res.headers.get("Content-Type", "").lower()
    if "application/pdf" in content_type or clean_url.lower().endswith(".pdf"):
        if pypdf is None:
            raise RuntimeError("PDF documents are not supported: pypdf library is not installed.")
        try:
            reader = pypdf.PdfReader(io.BytesIO(res.content))
            pages_text = []
            for p_idx, page in enumerate(reader.pages):
                pt = page.extract_text() or ""
                if pt.strip():
                    pages_text.append(pt)
                if len(pages_text) >= 50:
                    break
            pdf_text = "\n\n".join(pages_text)
        except Exception as pe:
            raise RuntimeError(f"Could not read PDF document: {str(pe)}")

        # Check if PDF contains email or phone / mobile number
        has_email = bool(re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', pdf_text))
        has_phone = bool(re.search(r'(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b', pdf_text))

        if not (has_email or has_phone):
            raise RuntimeError("PDF Skipped: Document contains no email addresses or mobile/phone numbers.")

        return pdf_text

    html = res.text

    # Auto-detect and submit department filter dropdowns (e.g. Southern Crescent Tech)
    html = auto_submit_department_form(clean_url, html)

    # Auto-detect directory pagination and crawl subsequent pages (e.g. South College, Wiregrass Tech)
    p_urls = discover_pagination_urls(clean_url, html, max_pages=20)
    if p_urls:
        html = crawl_pagination_pages(clean_url, html, p_urls)

    # 1. Vanderbilt University PeopleManager AJAX directory
    if "vanderbilt.edu" in clean_url.lower() or "js-people-directory" in html or "peoplemanager-directory" in html:
        try:
            vandy_api = "https://nursing.vanderbilt.edu/wp-content/themes/anchordown-futurevu-child-nursing/library/remote/peoplemanager-directory-remote-webcomm.php"
            v_params = {
                "option": "choose-department",
                "school": "9",
                "department": "all",
                "group": "31",
                "neighborhood": "none",
                "person": ""
            }
            v_res = requests.get(vandy_api, headers=BROWSER_HEADERS, params=v_params, timeout=20, verify=False)
            if v_res.status_code == 200:
                v_data = v_res.json().get("data", [])
                cards = []
                for p in v_data:
                    fn = p.get("full_name") or ""
                    titles = p.get("titles") or ""
                    em = p.get("email") or ""
                    ph = p.get("phone") or ""
                    office = p.get("office") or ""
                    bio = p.get("bio") or ""
                    cards.append(f"""
                    <div class="faculty-card">
                        <h3>{fn}</h3>
                        <p>Title: {titles}</p>
                        <p>Department: School of Nursing</p>
                        <p>Email: <a href="mailto:{em}">{em}</a></p>
                        <p>Phone: {ph}</p>
                        <p>Office: {office}</p>
                        <p>Bio: {bio[:200]}</p>
                    </div>
                    """)
                if cards:
                    html += "\n\n<!-- LOADED VANDERBILT PEOPLEMANAGER -->\n" + "\n".join(cards)
        except Exception:
            pass

    # 2. Augusta University faculty directory search portal
    if "augusta.edu" in clean_url.lower() or ("search.php" in html and ('name="college"' in html or "Filter by Unit" in html)):
        try:
            search_url = "https://www.augusta.edu/faculty/directory/search.php"
            post_data = {"college": "4", "first": "", "last": ""}
            post_res = requests.post(search_url, headers=BROWSER_HEADERS, data=post_data, timeout=20, verify=False)
            if post_res.status_code == 200 and len(post_res.text) > 10000:
                html = post_res.text
        except Exception:
            pass

    # 3. Chattahoochee Technical College XML directory
    if "chattahoocheetech.edu" in clean_url.lower() or "directory.xml" in html:
        try:
            xml_url = "https://www.chattahoocheetech.edu/_resources/data/directory/directory.xml"
            xml_res = requests.get(xml_url, headers=BROWSER_HEADERS, timeout=20, verify=False)
            if xml_res.status_code == 200:
                xml_soup = BeautifulSoup(xml_res.text, "xml")
                rows = xml_soup.find_all("ROW")
                cards = []
                for row in rows:
                    dept = row.find("Department").text if row.find("Department") else ""
                    if "nurs" in dept.lower() or "health" in dept.lower():
                        fn = row.find("First_Name").text if row.find("First_Name") else ""
                        ln = row.find("Last_Name").text if row.find("Last_Name") else ""
                        title = row.find("Job_Title").text if row.find("Job_Title") else ""
                        em = row.find("Email_Address").text if row.find("Email_Address") else ""
                        ph = f"{row.find('Phone_Area').text}-{row.find('Phone_Number').text}" if row.find("Phone_Area") and row.find("Phone_Number") else ""
                        campus = row.find("Campus").text if row.find("Campus") else ""
                        cards.append(f"""
                        <div class="faculty-card">
                            <h3>{fn} {ln}</h3>
                            <p>Title: {title}</p>
                            <p>Department: {dept}</p>
                            <p>Email: <a href="mailto:{em}">{em}</a></p>
                            <p>Phone: {ph}</p>
                            <p>Campus: {campus}</p>
                        </div>
                        """)
                if cards:
                    html += "\n\n<!-- LOADED CHATTAHOOCHEE DIRECTORY XML -->\n" + "\n".join(cards)
        except Exception:
            pass

    # 4. EssentialBlocks Pro Data Table (e.g. Gwinnett Tech)
    if "eb-data-table" in html or "EssentialBlocksProLocalize" in html:
        try:
            m_post = re.search(r'data-post-id=["\']([^"\']+)["\']', html)
            m_block = re.search(r'data-block-id=["\']([^"\']+)["\']', html)
            m_nonce = re.search(r'["\']data_table_nonce["\']\s*:\s*["\']([^"\']+)["\']', html)
            m_ajax = re.search(r'["\']ajax_url["\']\s*:\s*["\']([^"\']+)["\']', html)
            if m_post and m_block:
                post_id = m_post.group(1)
                block_id = m_block.group(1)
                nonce = m_nonce.group(1) if m_nonce else ""
                ajax_url = m_ajax.group(1).replace(r"\/", "/") if m_ajax else urljoin(clean_url, "/wp-admin/admin-ajax.php")
                post_payload = {
                    "action": "eb_data_table_post_meta",
                    "data_table_nonce": nonce,
                    "post_id": post_id,
                    "block_id": block_id
                }
                eb_res = requests.post(ajax_url, headers=BROWSER_HEADERS, data=post_payload, timeout=20, verify=False)
                if eb_res.status_code == 200:
                    eb_data = eb_res.json().get("data", {}).get(block_id, {})
                    rows = eb_data.get("rows", [])
                    cards = []
                    for row in rows:
                        row_str = " ".join(str(cell) for cell in row)
                        if "nurs" in row_str.lower() or "health" in row_str.lower():
                            em = next((str(c) for c in row if "@" in str(c)), "")
                            ph = next((str(c) for c in row if re.search(r'\d{3}[-.\s]\d{3}', str(c))), "")
                            name = f"{row[0]} {row[1]}" if len(row) >= 2 else str(row[0])
                            title = str(row[2]) if len(row) >= 3 else ""
                            dept = str(row[5]) if len(row) >= 6 else ""
                            cards.append(f"""
                            <div class="faculty-card">
                                <h3>{name}</h3>
                                <p>Title: {title}</p>
                                <p>Department: {dept}</p>
                                <p>Email: <a href="mailto:{em}">{em}</a></p>
                                <p>Phone: {ph}</p>
                            </div>
                            """)
                    if cards:
                        html += "\n\n<!-- LOADED ESSENTIALBLOCKS DATA TABLE -->\n" + "\n".join(cards)
        except Exception:
            pass

    # 5. West Georgia Technical College School of Nursing
    if "westgatech.edu" in clean_url.lower():
        try:
            w_url = "https://www.westgatech.edu/department/school-of-nursing/"
            w_res = requests.get(w_url, headers=BROWSER_HEADERS, timeout=20, verify=False)
            if w_res.status_code == 200 and len(w_res.text) > 5000:
                html += "\n\n<!-- LOADED WGTC NURSING DEPARTMENT -->\n" + w_res.text
        except Exception:
            pass

    # 6. Ogeechee Technical College Nursing faculty page
    if "ogeecheetech.edu" in clean_url.lower():
        try:
            o_url = "https://www.ogeecheetech.edu/academics/nursing"
            o_res = requests.get(o_url, headers=BROWSER_HEADERS, timeout=20, verify=False)
            if o_res.status_code == 200 and len(o_res.text) > 5000:
                html += "\n\n<!-- LOADED OGEECHEE NURSING DEPARTMENT -->\n" + o_res.text
        except Exception:
            pass

    # 7. College of Coastal Georgia Directory
    if "ccga.edu" in clean_url.lower() and "/about/directory" not in clean_url.lower():
        try:
            c_url = "https://www.ccga.edu/about/directory/"
            c_res = requests.get(c_url, headers=BROWSER_HEADERS, timeout=20, verify=False)
            if c_res.status_code == 200 and len(c_res.text) > 10000:
                html = c_res.text
        except Exception:
            pass

    # 8. Detect portal-gated private institutions (Chamberlain, Herzing, South University)
    if any(d in clean_url.lower() for d in ["chamberlain.edu", "herzing.edu", "southuniversity.edu"]):
        emails_check = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', html)
        if not emails_check or all(any(t in e.lower() for t in ["info@", "support@", "admin@", "admissions@", "inputmask", "transcripts@"]) for e in emails_check):
            raise RuntimeError("Portal-Gated Directory: This institution does not publish a public faculty directory. Faculty contact info is gated behind their internal portal (Canvas / Brightspace / MyChamberlain).")

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
        "vanderbilt.edu": "Vanderbilt University",
        "augusta.edu": "Augusta University",
        "ccga.edu": "College of Coastal Georgia",
        "chattahoocheetech.edu": "Chattahoochee Technical College",
        "gwinnetttech.edu": "Gwinnett Technical College",
        "westgatech.edu": "West Georgia Technical College",
        "ogeecheetech.edu": "Ogeechee Technical College",
        "centralgatech.edu": "Central Georgia Technical College",
        "northgatech.edu": "North Georgia Technical College",
        "laniertech.edu": "Lanier Technical College",
        "albanytech.edu": "Albany Technical College",
        "coastalpines.edu": "Coastal Pines Technical College",
        "southeasterntech.edu": "Southeastern Technical College",
        "southernregionaltech.edu": "Southern Regional Technical College",
        "wiregrass.edu": "Wiregrass Georgia Technical College",
        "lagrange.edu": "LaGrange College",
        "gntc.edu": "Georgia Northwestern Technical College",
        "asurams.edu": "Albany State University",
        "georgiasouthern.edu": "Georgia Southern University",
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
        "mercer.edu": "Mercer University",
        "brenau.edu": "Brenau University",
        "averett.edu": "Averett University",
        "south.edu": "South College",
        "southuniversity.edu": "South University",
        "chamberlain.edu": "Chamberlain University",
        "herzing.edu": "Herzing University",
        "jhu.edu": "Johns Hopkins University",
        "umich.edu": "University of Michigan"
    }
    clean_domain = domain.replace("www.", "").lower()
    for k, v in known_unis.items():
        if clean_domain == k or clean_domain.endswith("." + k):
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

    # Explicitly exclude directory pagination URLs from being treated as single profile links
    if re.search(r'/(?:page|paged?|p)/\d+/?$', path_lower) or re.search(r'[?&](?:page|p|pg|paged|page_number)=\d+', full_url.lower()):
        return False
    if path_segments and path_segments[-1].isdigit():
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
            r = requests.get(p_url, headers=BROWSER_HEADERS, timeout=8, verify=False)
            if r.status_code == 200:
                txt = sanitize_raw_html(r.text, base_domain)
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

        # If content is extracted from a PDF, it is already clean text
        if url.lower().endswith(".pdf") or not ("<html" in raw_html.lower() or "<body" in raw_html.lower()):
            clean_text = raw_html
        else:
            # If page contains a massive multi-department table, isolate nursing rows
            tr_blocks = re.findall(r'<tr[^>]*>.*?</tr>', raw_html, flags=re.DOTALL | re.IGNORECASE)
            processed_html = raw_html
            if len(tr_blocks) > 100:
                header_tr = tr_blocks[0] if tr_blocks else ""
                target_trs = [tr for tr in tr_blocks[1:] if "nurs" in tr.lower()]
                m = re.search(r'Contact Info.*?(?:Button HTML|<table)', raw_html, flags=re.DOTALL | re.IGNORECASE)
                top_contact = m.group(0) if m else ""
                processed_html = top_contact + "\n<table>\n" + header_tr + "\n" + "\n".join(target_trs) + "\n</table>"

            # Sanitize HTML into clean readable text
            clean_text = sanitize_raw_html(processed_html, domain)

            # Crawl individual faculty profile bio pages if direct dynamic feeds were not already loaded
            has_dynamic_feed = any(m in raw_html for m in ["LOADED VANDERBILT", "LOADED CHATTAHOOCHEE", "LOADED ESSENTIALBLOCKS", "LOADED WGTC", "LOADED OGEECHEE", "LOADED DYNAMIC JSON FEED"])
            if not has_dynamic_feed:
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
