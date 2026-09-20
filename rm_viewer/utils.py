import os
import sys
import logging
import argparse
from pathlib import Path

def setup_logger(log):
    blue = '\033[94m'
    yellow = '\033[93m'
    red = '\033[91m'
    magenta = '\033[95m'
    end = '\033[0m'

    class CustomFormatter(logging.Formatter):
        LEVEL_COLORS = {
            logging.INFO: blue,
            logging.WARNING: yellow,
            logging.ERROR: red,
            logging.CRITICAL: magenta
        }
        def format(self, record):
            color = self.LEVEL_COLORS.get(record.levelno, '')
            return f"{color}{super().format(record)}{end}"
    handler = logging.StreamHandler(sys.stdout)
    formatter = CustomFormatter(
        "%(asctime)s %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    log.setLevel(logging.INFO)
    # log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    log.propagate = False

def validate_path(path):
    if not os.path.isdir(path):
        raise argparse.ArgumentTypeError(f"{path} is not a valid path")
    return path

def validate_output_path(path):
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def get_gcv_api_key() -> str | None:
    """
    Get Google Cloud Vision API key from environment or config file.

    Checks in order:
    1. GCV_API_KEY environment variable
    2. ./gcv_api_key file

    :returns: API key string, or None if not found
    """
    # Check environment variable first
    api_key = os.environ.get('GCV_API_KEY')
    if api_key:
        return api_key.strip()

    # Fall back to config file
    config_path = Path('./gcv_api_key')
    if config_path.exists():
        return config_path.read_text().strip()

    return None
