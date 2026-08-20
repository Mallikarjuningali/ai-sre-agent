import os
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import MAX_RUN_HISTORY


OUTPUT_DIR = "output"
ARCHIVE_DIR = os.path.join(OUTPUT_DIR, "archive")

FOLDERS_TO_ARCHIVE = [
    "raw",
    "context",
    "reports",
    "logs",
    "summary",
    "prompts"
]


def archive_current_run():
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    timestamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d-%m-%Y_%H-%M-%S")

    run_folder = os.path.join(ARCHIVE_DIR, timestamp)

    os.makedirs(run_folder, exist_ok=True)

    for folder in FOLDERS_TO_ARCHIVE:
        source = os.path.join(OUTPUT_DIR, folder)

        if os.path.exists(source):
            destination = os.path.join(run_folder, folder)
            shutil.copytree(source, destination)

    cleanup_old_archives()


def cleanup_old_archives():
    runs = [
        os.path.join(ARCHIVE_DIR, d)
        for d in os.listdir(ARCHIVE_DIR)
        if os.path.isdir(os.path.join(ARCHIVE_DIR, d))
    ]

    runs.sort()

    while len(runs) > MAX_RUN_HISTORY:
        shutil.rmtree(runs[0])
        runs.pop(0)
