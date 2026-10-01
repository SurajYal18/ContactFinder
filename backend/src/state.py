# LangGraph state schema for university faculty directory scraper pipeline
from typing import TypedDict, List, Dict, Any, Optional

# TypedDict defining the state passed between LangGraph nodes
class ScraperState(TypedDict, total=False):
    # Target directory URL to scrape
    url: str
    # Raw HTML content retrieved from the web server
    raw_html: str
    # Cleaned and structured plain text extracted from HTML
    clean_text: str
    # Custom extraction instruction prompt for Gemini AI
    user_prompt: str
    # Institutional domain extracted from target URL
    domain: str
    # Resolved institutional or university name
    university: str
    # List of contact dictionaries extracted by Gemini AI
    contacts: List[Dict[str, Any]]
    # Output Excel filename
    output_file: str
    # Final filesystem path where the Excel file was saved
    saved_path: str
    # Maximum number of contacts to enrich with external APIs
    enrich_limit: Optional[int]
    # Current status message of the pipeline execution
    status: str
    # Error message if any step failed
    error: Optional[str]
