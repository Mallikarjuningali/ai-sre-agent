import json
from pathlib import Path

from utils.path_safety import validate_file_id


class ReportWriter:

    def __init__(self):

        self.output_dir = Path("output/reports")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(self, instance_id, report):

        file_path = self.output_dir / f"{validate_file_id(instance_id, 'resource_id')}.json"

        with open(file_path, "w") as f:
            json.dump(report, f, indent=4)

        return file_path
