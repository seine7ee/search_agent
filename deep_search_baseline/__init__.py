"""Single-agent ReAct baseline for deep-search trajectory synthesis."""

import logging

from .orchestrator import BaselineConfig, BaselineOrchestrator

logging.getLogger("deep_search_baseline").addHandler(logging.NullHandler())

__all__ = ["BaselineConfig", "BaselineOrchestrator"]
