"""
=========================================================
AI SRE AGENT
Module : JSON Writer
Author : Mallikarjun
Purpose:
    Write collector output to JSON files.
=========================================================
"""

# =========================================================
# Import Required Libraries
# =========================================================

import json
import os

# =========================================================
# Create Output Directory
# =========================================================

OUTPUT_DIR = "output/raw"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# =========================================================
# Write JSON File
# =========================================================

def write_json(filename, data):
    """
    Write data to JSON file.
    """

    filepath = os.path.join(
        OUTPUT_DIR,
        filename
    )

    with open(filepath, "w") as file:

        json.dump(
            data,
            file,
            indent=4,
            default=str
        )

    print(f"\nJSON written to {filepath}")
