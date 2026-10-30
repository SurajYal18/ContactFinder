# ContactCollectorAndEnricher

An automated higher education directory scraper, directory resolver, and contact discovery platform powered by FastAPI, LangGraph, Google Gemini AI, Hunter.io, and RocketReach.

---

## Table of Contents

- [Overview & Features](#overview--features)
- [Project Architecture](#project-architecture)
- [Environment Variables](#environment-variables)
- [Local Development Setup](#local-development-setup)
- [Deployment to Vercel](#deployment-to-vercel)
- [Security & Authentication](#security--authentication)
- [Troubleshooting & FAQs](#troubleshooting--faqs)

---

## Overview & Features

- **Automated Directory Scraping**: Crawls university and departmental directories with dynamic pagination handling, sub-profile bio parsing, and intelligent DOM filtering.
- **Built-in Email Deobfuscation Engine**:
  - Automatically identifies and decodes **TerminalFour** Caesar shifted emails (`class="emailob"` with `data-tel` phone cipher).
  - Decodes **Cloudflare** email protection (`data-cfemail` XOR cipher and `/cdn-cgi/l/email-protection` redirects).
  - Decodes **JavaScript substitution ciphers** and DOM `data-name`/`data-domain` attributes.
  - Automatically unescapes HTML entities (`&commat;`, `&#64;`, etc.) and URL percent-encoded `mailto:` strings.
  - Features a post-extraction safety net that applies Caesar shift brute-force (-15 to +15) on obfuscated email strings to restore valid institutional domains.
- **Directory Finder**: Resolves and verifies official faculty directory URLs directly from institution names or uploaded documents (Excel, CSV, PDF, TXT).
- **AI Extraction**: Uses Google Gemini (`gemini-3.5-flash-lite`) to extract faculty, staff, and leadership into structured 8-column data.
- **Dual Email Enrichment**: Integrates with **Hunter.io** and **RocketReach** for secondary email discovery and verification.
- **Client-Side Export**: Instant Excel (`.xlsx`) and CSV generation in the browser via SheetJS, with zero persistent server storage.
- **Secure Web Dashboard**: Role-based access control with SHA-256 HMAC cookie and bearer token authentication.

---

## Project Architecture

```
├── api/
│   └── index.py            # Vercel serverless entrypoint
├── backend/
│   ├── src/
│   │   ├── nodes/
│   │   │   ├── fetcher.py   # DOM fetching, pagination, and email deobfuscation
│   │   │   ├── extractor.py # Gemini AI structured extraction & email cleanup
│   │   │   ├── enricher.py  # Hunter.io & RocketReach email enrichment
│   │   │   └── exporter.py  # Excel export & temp file management
│   │   ├── finder.py        # University directory URL resolver & batch parser
│   │   ├── state.py         # LangGraph pipeline state definitions
│   │   └── workflow.py      # LangGraph state machine workflow
│   ├── server.py           # FastAPI application & authentication endpoints
│   ├── requirements.txt    # Python backend dependencies
│   └── .env.example        # Environment variable template
├── frontend/
│   ├── index.html          # Contact Finder UI dashboard
│   ├── directory.html      # Directory Finder UI dashboard
│   └── login.html          # Secure login portal
├── vercel.json             # Vercel deployment routing & service definitions
└── requirements.txt        # Root project dependencies
```

---

## Environment Variables

Configure the following variables in your `.env` file (for local testing) or in the **Environment Variables** settings in your Vercel project:

| Variable | Required | Description | Example |
| :--- | :---: | :--- | :--- |
| `GEMINI_API_KEY` | **Yes** | Google Gemini API key used for structured extraction | `AIzaSy...` |
| `AUTH_USERNAME` | **Yes** | Username required to log into the dashboard | `admin` |
| `AUTH_PASSWORD` | **Yes** | Secure password required to log into the dashboard | `StrongPassword123!` |
| `AUTH_SECRET_KEY` | Recommended | Random secret string used to sign SHA-256 session tokens | `64_char_hex_random_key` |
| `HUNTER_API_KEY` | Optional | Hunter.io API key for email lookup and verification | `f4a8b...` |
| `ENABLE_HUNTER` | Optional | Enable or disable Hunter.io enrichment (`true` or `false`) | `true` |
| `ROCKETREACH_API_KEY` | Optional | RocketReach API key for contact discovery | `4d246...` |
| `ENABLE_ROCKETREACH` | Optional | Enable or disable RocketReach enrichment (`true` or `false`) | `false` |

> [!TIP]
> Generate a strong 32-byte secret key for `AUTH_SECRET_KEY` by running:
> ```bash
> python -c "import secrets; print(secrets.token_hex(32))"
> ```

---

## Local Development Setup

1. **Clone repository:**
   ```bash
   git clone https://github.com/Mobiuso/ContactCollectorAndEnricher.git
   cd ContactCollectorAndEnricher
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment:**
   Create a `.env` file in the `backend/` directory:
   ```bash
   cp backend/.env.example backend/.env
   ```

4. **Run the local server:**
   ```bash
   python backend/server.py
   ```
   Open your browser at `http://127.0.0.1:8000`.

---

## Deployment to Vercel

The application is fully pre-configured for **Vercel** using [`vercel.json`](file:///d:/Scrapping/vercel.json), which orchestrates two integrated services:
- **Frontend Service**: Statically serves the HTML dashboards (`/`, `/directory`, `/login`).
- **Backend Service**: Routes all `/api/(.*)` requests directly to the FastAPI serverless backend (`api/index.py` and `backend/server.py`).

### Step-by-Step Deployment:

1. **Push Changes to GitHub**:
   Ensure your latest code is pushed to your GitHub repository (`main` branch).

2. **Import Repository into Vercel**:
   - Go to your [Vercel Dashboard](https://vercel.com).
   - Click **Add New...** > **Project**.
   - Select your repository and click **Import**.

3. **Configure Build Settings**:
   - **Framework Preset**: Leave as **Other** (Vercel automatically detects `vercel.json`).
   - **Root Directory**: `./` (default).

4. **Add Environment Variables**:
   Under **Settings** > **Environment Variables** in Vercel, add:
   - `GEMINI_API_KEY`
   - `AUTH_USERNAME`
   - `AUTH_PASSWORD`
   - `AUTH_SECRET_KEY`
   - `HUNTER_API_KEY` (if using Hunter.io)
   - `ENABLE_HUNTER` (`true` or `false`)
   - `ROCKETREACH_API_KEY` (if using RocketReach)
   - `ENABLE_ROCKETREACH` (`true` or `false`)

5. **Deploy**:
   - Click **Deploy**. Vercel will build both the frontend and backend serverless endpoints automatically.
   - Once deployment finishes, open your production URL (e.g. `https://your-project.vercel.app`) and sign in using your configured `AUTH_USERNAME` and `AUTH_PASSWORD`.

> [!NOTE]
> Serverless function execution times on Vercel's free tier are limited to 15 seconds (up to 300s on Pro plans). For long-running batch extraction jobs with extensive pagination, consider increasing the function duration in project settings or running on a Pro plan.

---

## Security & Authentication

- **Token Signing**: Session tokens are cryptographically generated using SHA-256 HMAC based on `AUTH_USERNAME`, `AUTH_PASSWORD`, and `AUTH_SECRET_KEY`.
- **Stateless Verification**: The backend verifies incoming session tokens via:
  1. `Authorization: Bearer <token>` header
  2. `X-Auth-Token` custom header
  3. `auth_token` HTTP cookie
- **Protected Routes**: All operational API endpoints (`/api/scrape`, `/api/find-directories`, `/api/extract-schools-from-file`) require valid authentication.
- **Zero-Disk Data Policy**: Contact datasets are returned directly to the client browser as structured JSON and downloaded as Excel via client-side JavaScript, leaving no personal data on the server disk.

---

## Troubleshooting & FAQs

### Q: Why do I get a 401 Unauthorized error?
Ensure `AUTH_USERNAME` and `AUTH_PASSWORD` are configured in your Vercel Project Settings > Environment Variables. If you update credentials, log out from `/login` and sign back in to refresh your authentication cookie.

### Q: How does the tool handle protected or encoded emails?
The scraper includes an automated deobfuscation engine that unpacks:
1. TerminalFour / Caesar shifted strings (e.g., `badkk0?tl`rrc-dct` $\rightarrow$ `cbell1@umassd.edu`)
2. Cloudflare XOR encrypted addresses
3. URL percent-encoded and HTML character entities

### Q: Why did a scrape time out on Vercel?
Vercel Serverless functions have execution timeout limits (15 seconds on Hobby, up to 300 seconds on Pro). If you are scraping a directory with dozens of paginated sub-pages, you can increase the function timeout in Vercel Project Settings > Functions.
