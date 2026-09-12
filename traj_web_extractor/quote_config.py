"""Configuration for selecting the per-web relevance extraction strategy."""

from __future__ import annotations


VERBATIM_MODE = "verbatim"
SENTENCE_IDS_MODE = "sentence_ids"
EXTRACTION_MODES = (VERBATIM_MODE, SENTENCE_IDS_MODE)

QWEN_MODEL_PROVIDER = "qwen"
DS_MODEL_PROVIDER = "ds"
MODEL_PROVIDERS = (QWEN_MODEL_PROVIDER, DS_MODEL_PROVIDER)

# Change this value to switch the default used by the Python API and runners.
# Individual API/CLI calls can still override it without editing this file.
DEFAULT_EXTRACTION_MODE = SENTENCE_IDS_MODE
# qwen -> req_qwen.py:req_qwen_model; ds -> req_ds.py:request_model.
DEFAULT_MODEL_PROVIDER = DS_MODEL_PROVIDER


def resolve_extraction_mode(extraction_mode: str | None) -> str:
    """Return a validated mode, falling back to the configured default."""
    mode = DEFAULT_EXTRACTION_MODE if extraction_mode is None else extraction_mode
    if not isinstance(mode, str) or mode not in EXTRACTION_MODES:
        choices = ", ".join(EXTRACTION_MODES)
        raise ValueError(f"extraction_mode must be one of: {choices}")
    return mode


def resolve_model_provider(model_provider: str | None) -> str:
    """Return a validated extraction-model provider."""
    provider = DEFAULT_MODEL_PROVIDER if model_provider is None else model_provider
    if not isinstance(provider, str) or provider not in MODEL_PROVIDERS:
        choices = ", ".join(MODEL_PROVIDERS)
        raise ValueError(f"model_provider must be one of: {choices}")
    return provider
