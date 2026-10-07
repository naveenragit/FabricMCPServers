"""Suppress third-party payload-bearing diagnostics for this data-facing POC."""

import logging
import sys


def configure_logging() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
    # SDK validation/transport errors can log input_value containing private data
    # before our safe exception boundary sees them. Retain only our safe messages.
    manager = logging.Logger.manager
    prefixes = ("mcp", "azure", "httpx", "httpcore")
    for name in list(manager.loggerDict):
        if name.startswith(prefixes):
            logger = logging.getLogger(name)
            logger.disabled = True
            logger.handlers = [logging.NullHandler()]
            logger.propagate = False
    for name in prefixes:
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL + 1)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False