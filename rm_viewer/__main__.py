import sys
import os
import logging
import argparse

from pathlib import Path

log = logging.getLogger(__package__)

from .utils import setup_logger

from .rm_process import build_process_parser, rm_process
from .rm_view import build_view_parser, rm_view
from .syncd import build_syncd_parser, rm_syncd

def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments for PeAR

    :returns: parsed arguments
    """
    parser = argparse.ArgumentParser(
        description='Process and view files from Remarkable tablet'
    )

    subparser = parser.add_subparsers(dest='action',
                                      help='Available actions',
                                      required=True)
    build_process_parser(subparser)
    build_view_parser(subparser)
    build_syncd_parser(subparser)
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    setup_logger(log)
    args = parse_args()

    if args.action == 'processor':
        rm_process(args)

    if args.action == 'view':
        rm_view(args)

    if args.action == 'syncd':
        rm_syncd(args)
