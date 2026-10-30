# ContactCollectorAndEnricher

An automated higher education directory scraper, directory resolver, and contact discovery platform powered by FastAPI, LangGraph, Google Gemini AI, Hunter.io, and RocketReach.

---

## Table of Contents

- [Overview & Features](#overview--features)
- [Project Architecture](#project-architecture)
- [Environment Variables](#environment-variables)
- [Local Development Setup](#local-development-setup)
- [Deployment Guide](#deployment-guide)
  - [1. Vercel Serverless Deployment (Recommended)](#1-vercel-serverless-deployment-recommended)
  - [2. Docker Deployment](#2-docker-deployment)
  - [3. Linux VPS / AWS EC2 Deployment](#3-linux-vps--aws-ec2-deployment)
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

Configure the following variables in your `.env` file (for local/server execution) or in the **Environment Variables** settings of your cloud hosting provider:

| Variable | Required | Description | Example |
| :--- | :---: | :--- | :--- |
| `GEMINI_API_KEY` | **Yes** | Google Gemini API key used for structured extraction | `AIzaSy...` |
| `AUTH_USERNAME` | **Yes** | Username required to access the dashboard | `admin` |
| `AUTH_PASSWORD` | **Yes** | Secure password required to access the dashboard | `StrongPassword123!` |
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

### 1. Clone the Repository
```bash
git clone https://github.com/Mobiuso/ContactCollectorAndEnricher.git
cd ContactCollectorAndEnricher
```

### 2. Set Up a Virtual Environment
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
Copy the example environment template and fill in your keys:
```bash
cp backend/.env.example backend/.env
```

### 5. Launch the Application
```bash
python backend/server.py
```
Open your browser at `http://127.0.0.1:8000` to access the dashboard.

---

## Deployment Guide

### 1. Vercel Serverless Deployment (Recommended)

The repository includes a ready-to-use [`vercel.json`](file:///d:/Scrapping/vercel.json) that configures two integrated services:
- **Frontend**: Serves static HTML pages (`/`, `/directory`, `/login`).
- **Backend**: Routes `/api/*` to the FastAPI application.

#### Step-by-Step Vercel Setup:

1. **Push your code to GitHub**:
   Ensure your changes are committed and pushed to your GitHub repository.

2. **Import into Vercel**:
   - Log into your [Vercel Dashboard](https://vercel.com).
   - Click **Add New...** > **Project**.
   - Select your repository and click **Import**.

3. **Configure Project Settings**:
   - **Framework Preset**: Leave as **Other** (Vercel automatically detects `vercel.json`).
   - **Root Directory**: `./` (default).

4. **Add Environment Variables**:
   Under **Settings** > **Environment Variables**, add:
   - `GEMINI_API_KEY`
   - `AUTH_USERNAME`
   - `AUTH_PASSWORD`
   - `AUTH_SECRET_KEY`
   - `HUNTER_API_KEY` (if using Hunter.io)
   - `ENABLE_HUNTER` (set to `true` or `false`)
   - `ROCKETREACH_API_KEY` (if using RocketReach)
   - `ENABLE_ROCKETREACH` (set to `true` or `false`)

5. **Deploy**:
   - Click **Deploy**. Vercel will build the frontend and backend serverless endpoints.
   - Once deployed, visit `https://your-project.vercel.app` and sign in with your credentials.

> [!NOTE]
> Serverless function execution times on Vercel's free tier are limited to 15 seconds (up to 300s on Pro plans). For long-running batch extraction jobs with extensive pagination, a persistent VPS or Docker deployment is recommended.

---

### 2. Docker Deployment

To deploy anywhere using Docker (Render, Railway, Fly.io, or AWS ECS):

#### Create a `Dockerfile`:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose server port
EXPOSE 8000

# Set environment
ENV PYTHONUNBUFFERED=1

# Launch FastAPI via Uvicorn
CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### Build and Run:
```bash
# Build image
docker build -t contact-collector .

# Run container with environment file
docker run -d --name contact-collector \
  -p 8000:8000 \
  --env-file backend/.env \
  contact-collector
```

---

### 3. Linux VPS / AWS EC2 Deployment

For a dedicated persistent Linux server (Ubuntu 22.04+):

#### Step 1: System Preparation
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git nginx
```

#### Step 2: Clone and Install
```bash
cd /opt
sudo git clone https://github.com/Mobiuso/ContactCollectorAndEnricher.git
cd ContactCollectorAndEnricher
sudo chown -R $USER:$USER /opt/ContactCollectorAndEnricher

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp backend/.env.example backend/.env
nano backend/.env  # Enter your API keys and credentials
```

#### Step 3: Create a Systemd Service
Create `/etc/systemd/system/contact-collector.service`:
```ini
[Unit]
Description=Contact Collector and Enricher FastAPI Server
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/ContactCollectorAndEnricher
EnvironmentFile=/opt/ContactCollectorAndEnricher/backend/.env
ExecStart=/opt/ContactCollectorAndEnricher/venv/bin/uvicorn backend.server:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable contact-collector
sudo systemctl start contact-collector
```

#### Step 4: Configure Nginx Reverse Proxy
Create `/etc/nginx/sites-available/contact-collector`:
```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 300s;
    }
}
```
Enable the site and enable SSL via Let's Encrypt:
```bash
sudo ln -s /etc/nginx/sites-available/contact-collector /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d yourdomain.com
```

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
Ensure `AUTH_USERNAME` and `AUTH_PASSWORD` are configured in your `.env` or Vercel environment variables. If you change credentials, log out from `/login` and sign back in to refresh your authentication cookie.

### Q: How does the tool handle protected or encoded emails?
The scraper includes an automated deobfuscation node that unpacks:
1. TerminalFour / Caesar shifted strings (e.g. `badkk0?tl`rrc-dct` $\rightarrow$ `cbell1@umassd.edu`)
2. Cloudflare XOR encrypted addresses
3. URL percent-encoded and HTML character entities

### Q: Why did a scrape time out on Vercel?
Vercel Serverless functions have execution timeout limits (15 seconds on Hobby, up to 300 seconds on Pro). If you are scraping a directory with dozens of paginated sub-pages, either upgrade function timeout in `vercel.json` or deploy to a VPS / Docker environment.
