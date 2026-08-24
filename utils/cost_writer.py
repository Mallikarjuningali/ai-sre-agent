"""
=========================================================
AI SRE AGENT
Module : Cost JSON Writer
Purpose:
    Write Cost Explorer collector output to JSON files, completely
    separate from utils/writer.py (which hardcodes output/raw at module
    scope and is used by the infra collectors only).
=========================================================
"""

import json
import os

OUTPUT_DIR = "output/cost/raw"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def write_json(filename, data):
    """
    Write data to a JSON file under output/cost/raw/.
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
