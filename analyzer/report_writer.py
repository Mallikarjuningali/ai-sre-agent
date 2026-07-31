import json
from pathlib import Path


class ReportWriter:

    def __init__(self):

        self.output_dir = Path("output/reports")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(self, instance_id, report):

        file_path = self.output_dir / f"{instance_id}.json"

        with open(file_path, "w") as f:
            json.dump(report, f, indent=4)

        return file_path
