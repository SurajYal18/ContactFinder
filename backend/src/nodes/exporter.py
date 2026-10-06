# LangGraph node to export extracted contacts into a formatted 7-column Excel workbook
import os
import tempfile
import pandas as pd
from src.state import ScraperState

# Formats and exports contact records into an Excel file
def export_contacts_to_excel(contacts: list, output_filename: str) -> str:
    rows = []
    for c in contacts:
        rows.append({
            "First Name": str(c.get("first_name") or "").strip(),
            "Last Name": str(c.get("last_name") or "").strip(),
            "Credentials": str(c.get("credentials") or c.get("credential") or "").strip(),
            "Title": str(c.get("title") or "").strip(),
            "Campus": str(c.get("campus") or "").strip(),
            "Email": str(c.get("email") or "").strip(),
            "Phone": str(c.get("phone") or "").strip()
        })

    # Predefine exact standard 7 columns
    columns = ["First Name", "Last Name", "Credentials", "Title", "Campus", "Email", "Phone"]
    df = pd.DataFrame(rows, columns=columns)

    # Determine valid writable destination path
    out_dir = os.path.dirname(os.path.abspath(output_filename))
    base_name = os.path.basename(output_filename)

    # If output_filename directory is not writable or running on Vercel, use tempdir exports
    if not os.access(out_dir, os.W_OK) or os.getenv("VERCEL"):
        exports_temp = os.path.join(tempfile.gettempdir(), "exports")
        os.makedirs(exports_temp, exist_ok=True)
        target_path = os.path.join(exports_temp, base_name)
    else:
        os.makedirs(out_dir, exist_ok=True)
        target_path = os.path.abspath(output_filename)

    # Save to Excel workbook via openpyxl
    df.to_excel(target_path, index=False, engine="openpyxl")

    # Also save a mirror copy directly in tempfile.gettempdir() for cross-path compatibility
    try:
        direct_tmp = os.path.join(tempfile.gettempdir(), base_name)
        if os.path.abspath(direct_tmp) != os.path.abspath(target_path):
            df.to_excel(direct_tmp, index=False, engine="openpyxl")
    except Exception:
        pass

    return os.path.abspath(target_path)

# LangGraph node function for exporting contacts
def export_node(state: ScraperState) -> ScraperState:
    contacts = state.get("contacts", [])
    output_file = state.get("output_file") or "nursing_faculty.xlsx"

    if not output_file.endswith(".xlsx"):
        output_file += ".xlsx"

    # Always use writable temp dir if running on Vercel or relative path
    if not os.path.isabs(output_file) or os.getenv("VERCEL"):
        exports_temp = os.path.join(tempfile.gettempdir(), "exports")
        os.makedirs(exports_temp, exist_ok=True)
        output_file = os.path.join(exports_temp, os.path.basename(output_file))

    try:
        saved_path = export_contacts_to_excel(contacts, output_file)
        return {
            "saved_path": saved_path,
            "status": f"Successfully exported {len(contacts)} contacts to {saved_path}"
        }
    except Exception as e:
        # Prevent export failure from crashing the workflow and dropping extracted contacts
        return {
            "saved_path": "",
            "status": f"Extracted {len(contacts)} contacts (file save warning: {str(e)})"
        }
