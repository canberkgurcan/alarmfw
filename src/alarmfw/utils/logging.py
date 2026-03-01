import logging
import sys
import time

def setup_logging(level: str = "INFO") -> None:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)sZ %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )
