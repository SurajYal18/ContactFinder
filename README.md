# ContactFinder - Directory Contact Scraper

An automated university directory scraper and contact discovery service powered by FastAPI, LangGraph, Google Gemini AI, and Hunter.io.

## Features

- **Automated Directory Scraping**: Crawls university and departmental directories with HTML sanitization.
- **AI Extraction**: Uses Google Gemini 2.5 Flash Lite to extract faculty, staff, and leadership into structured data.
- **Contact Enrichment**: Integrates with Hunter.io to discover verified email addresses and domains.
- **Excel Spreadsheet Export**: Exports records directly into Excel workbooks (`.xlsx`).
- **Modern Web Dashboard**: Real-time extraction progress, searchable records table, and direct download links.
- **Serverless Ready**: Pre-configured for deployment on Vercel with Python serverless functions.

## Project Structure

```
├── api/
│   └── index.py            # Vercel serverless entrypoint
├── backend/
│   ├── src/                # LangGraph nodes and workflows
│   ├── server.py           # FastAPI application
│   └── requirements.txt    # Backend Python dependencies
├── frontend/
│   └── index.html          # Single-page web dashboard
├── vercel.json             # Vercel deployment configuration
└── requirements.txt        # Root dependencies for Vercel Python runtime
```

## Local Development

1. **Clone repository:**
   ```bash
   git clone https://github.com/SurajYal18/ContactFinder.git
   cd ContactFinder
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables:**
   Create a `.env` file in the `backend/` directory:
   ```env
   GEMINI_API_KEY=your_gemini_api_key
   HUNTER_API_KEY=your_hunter_api_key
   ENABLE_HUNTER=true
   ```

4. **Run the server:**
   ```bash
   python backend/server.py
   ```
   Open your browser at `http://127.0.0.1:8000`.

## Deployment to Vercel

1. Import this repository into [Vercel](https://vercel.com).
2. Set Environment Variables in Project Settings:
   - `GEMINI_API_KEY`
   - `HUNTER_API_KEY`
   - `ENABLE_HUNTER`
3. Click **Deploy**.
