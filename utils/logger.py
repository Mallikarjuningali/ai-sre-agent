import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

LOG_DIR = "output/logs"
os.makedirs(LOG_DIR, exist_ok=True)

timestamp = datetime.now(
    ZoneInfo("Asia/Kolkata")
).strftime("%d-%m-%Y_%H-%M-%S")

log_file = os.path.join(LOG_DIR, f"{timestamp}.log")


def get_logger(name):

    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.propagate = False

    return logger
