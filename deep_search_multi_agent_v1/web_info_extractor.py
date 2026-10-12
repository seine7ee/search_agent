"""Per-webpage compression: keep only what is relevant to a search intent.

Retrofit of ``traj_web_extractor/quote_extractor.py``'s sentence-range
extraction mode (see ``traj_web_extractor/sentence_range_quotes.py``) for
this project's stateless-per-round pipeline:

* The original takes a fixed ``search_goal`` string. Here the caller passes
  whatever the current "search intent" is for this round -- the goal's own
  ``search_goal`` text, or a more specific ``search_focus`` when the
  orchestrator has one (round >= 2's SEARCH action) -- under a single
  ``search_intent`` parameter, exactly as ``build_sentence_range_messages``'s
  own ``search_goal`` argument already accepts either ("当前搜索重点").
* The original's model call returns a raw string
  ``"[start,end]|[start,end]"`` (or ``"[]"``) and is parsed with
  ``parse_sentence_ranges``. This project's ``request_model(messages) ->
  dict`` convention (see ``get_model_infer.py``) always parses a JSON
  *object*, so here the model instead returns ``{"sentence_ranges":
  [[start,end], ...]}`` -- same semantics, JSON-object-shaped so it fits the
  same call/retry plumbing as every other model role in this project.
* The original returns a bare list of verbatim quote spans. Here the spans
  are joined with a blank line into one compressed string that replaces the
  page's content wherever it is shown to the Planner -- callers that get an
  empty string back should drop that page entirely (nothing on it was
  relevant to this round).

Sentence splitting, span reconstruction and range merging are reused as-is
from ``traj_web_extractor`` rather than re-implemented: that CJK-aware
sentence-boundary logic is nontrivial and already covered by its own tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from traj_web_extractor.sentence_id_quotes import NumberedSentence, format_numbered_sentences, split_web_content
from traj_web_extractor.sentence_range_quotes import SentenceRangeFormatError, rebuild_range_quotes

try:
    from .prompts import WEB_INFO_EXTRACTOR_SYSTEM_PROMPT
except ImportError:  # pragma: no cover - direct script execution.
    from prompts import WEB_INFO_EXTRACTOR_SYSTEM_PROMPT


class WebInfoFormatError(ValueError):
    """The model did not return a valid ``{"sentence_ranges": [...]}`` object."""


def build_web_info_messages(
    user_query: str,
    search_intent: str,
    sentences: list[NumberedSentence],
) -> list[dict[str, str]]:
    """Build one stateless model context for a single (query, intent, webpage)."""
    if not isinstance(user_query, str) or not user_query.strip():
        raise ValueError("user_query must be a non-empty string")
    if not isinstance(search_intent, str) or not search_intent.strip():
        raise ValueError("search_intent must be a non-empty string")
    content = (
        f"user_query：{user_query}\n"
        f"当前搜索目标/重点：{search_intent}\n"
        "搜索网页（句子编号仅用于本次抽取）：\n"
        f"<numbered_web_content>\n{format_numbered_sentences(sentences)}"
        "\n</numbered_web_content>"
    )
    return [
        {"role": "system", "content": WEB_INFO_EXTRACTOR_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _extract_sentence_ranges(response: Mapping[str, Any]) -> list[list[int]]:
    if not isinstance(response, Mapping):
        raise WebInfoFormatError("web_info_extractor must return a JSON object")
    raw_ranges = response.get("sentence_ranges")
    if not isinstance(raw_ranges, list):
        raise WebInfoFormatError("sentence_ranges must be an array")
    ranges: list[list[int]] = []
    for index, item in enumerate(raw_ranges):
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in item)
        ):
            raise WebInfoFormatError(f"sentence_ranges[{index}] must be [start, end] integers")
        ranges.append(list(item))
    return ranges


def compress_response(
    response: Mapping[str, Any],
    content: str,
    sentences: list[NumberedSentence],
) -> str:
    """Turn one model response into the compressed page text (``""`` if irrelevant)."""
    sentence_ranges = _extract_sentence_ranges(response)
    try:
        spans = rebuild_range_quotes(content, sentences, sentence_ranges)
    except SentenceRangeFormatError as exc:
        raise WebInfoFormatError(str(exc)) from exc
    return "\n\n".join(spans)


__all__ = [
    "WebInfoFormatError",
    "NumberedSentence",
    "split_web_content",
    "build_web_info_messages",
    "compress_response",
]
