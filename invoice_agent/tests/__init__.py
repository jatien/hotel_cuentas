"""Tests de invoice_agent. Los logs estructurados se silencian para no ensuciar la salida."""

import logging

logging.getLogger("invoice_agent").setLevel(logging.CRITICAL)
