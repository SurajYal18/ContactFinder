# LangGraph workflow definition chaining fetch, Gemini AI extraction, enrichment, and Excel export
from langgraph.graph import StateGraph, START, END
from src.state import ScraperState
from src.nodes.fetcher import fetch_node
from src.nodes.extractor import extract_node
from src.nodes.enricher import enrich_node
from src.nodes.exporter import export_node

# Router condition determining if an error occurred to short-circuit execution
def should_continue(state: ScraperState) -> str:
    return END if state.get("error") else "next"

# Constructs and compiles the LangGraph StateGraph pipeline
def create_scraper_graph():
    builder = StateGraph(ScraperState)

    # Register workflow nodes
    builder.add_node("fetch", fetch_node)
    builder.add_node("extract", extract_node)
    builder.add_node("enrich", enrich_node)
    builder.add_node("export", export_node)

    # Connect START to fetch node
    builder.add_edge(START, "fetch")

    # Connect fetch to extractor with error routing
    builder.add_conditional_edges("fetch", should_continue, {"next": "extract", END: END})

    # Connect extractor to enricher with error routing
    builder.add_conditional_edges("extract", should_continue, {"next": "enrich", END: END})

    # Connect enricher to exporter
    builder.add_edge("enrich", "export")

    # Connect exporter to END
    builder.add_edge("export", END)

    # Compile the LangGraph pipeline
    return builder.compile()
