"""Load quote-extraction JSONL and serve the local inspection interface."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


MODULE_DIR = Path(__file__).resolve().parent
VIEWER_DIR = MODULE_DIR / "viewer"
DEFAULT_QUOTES_DIR = MODULE_DIR / "webs_quotes"
DEFAULT_QUOTES_FILE = MODULE_DIR / "webs_quotes" / (
    "sentence_ids_ds_traj_小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？_"
    "2026-08-24_1787535881631__1789201544726_99a5bcbb9876_quotes.jsonl"
)

_QUERY_FROM_NAME = re.compile(
    r"(?:^|_)traj_(.+?)_(?:\d{4}-\d{2}-\d{2}|\d{4}年\d{2}月\d{2}日)_"
)


class ViewerDataError(ValueError):
    """The extraction result cannot be represented safely by the viewer."""


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as source:
            value = json.load(source)
    except json.JSONDecodeError as exc:
        raise ViewerDataError(f"{label} is not valid JSON: {path} ({exc})") from exc
    if not isinstance(value, dict):
        raise ViewerDataError(f"{label} must contain a JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        source = path.open("r", encoding="utf-8-sig")
    except OSError as exc:
        raise ViewerDataError(f"cannot open quotes JSONL: {path} ({exc})") from exc
    with source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ViewerDataError(
                    f"invalid JSONL record at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(value, dict):
                raise ViewerDataError(f"JSONL line {line_number} must be an object")
            value = deepcopy(value)
            value["record_index"] = len(records) + 1
            records.append(value)
    return records


def find_search_goals_file(quotes_path: str | Path) -> Path | None:
    """Resolve the automatically paired search_goals file when it exists."""
    path = Path(quotes_path).expanduser().resolve()
    suffix = "_quotes.jsonl"
    if not path.name.endswith(suffix):
        return None
    companion_name = f"{path.name.removesuffix(suffix)}_search_goals.json"
    if path.parent.name == "webs_quotes":
        candidate = path.parent.parent / "search_goals" / companion_name
    else:
        candidate = path.with_name(companion_name)
    return candidate if candidate.is_file() else None


def _query_from_filename(path: Path) -> str | None:
    match = _QUERY_FROM_NAME.search(path.name)
    return match.group(1) if match else None


def _resolve_user_query(
    quotes_path: Path,
    search_goals_path: str | Path | None,
) -> tuple[str, Path | None, list[str]]:
    warnings: list[str] = []
    if search_goals_path is None:
        companion = find_search_goals_file(quotes_path)
    else:
        companion = Path(search_goals_path).expanduser().resolve()
        if not companion.is_file():
            raise ViewerDataError(f"search_goals file does not exist: {companion}")
    if companion is not None:
        document = _load_json_object(companion, "search_goals file")
        query = document.get("user_query")
        if isinstance(query, str) and query.strip():
            return query.strip(), companion, warnings
        warnings.append("配套 search_goals 文件没有有效的 user_query。")

    inferred = _query_from_filename(quotes_path)
    if inferred:
        warnings.append("未找到可用的配套 search_goals 文件，用户问题由文件名推断。")
        return inferred, companion, warnings
    warnings.append("未找到用户问题；请通过 --search-goals 指定配套文件。")
    return "未找到用户问题", companion, warnings


def _list(value: Any, field: str, record_index: int) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ViewerDataError(f"record {record_index}: {field} must be an array")
    return value


def _validate_record(record: dict[str, Any]) -> dict[str, Any]:
    record_index = record["record_index"]
    goal_id = record.get("search_goal_id")
    search_goal = record.get("search_goal")
    web = record.get("web")
    if not isinstance(goal_id, (str, int)) or isinstance(goal_id, bool):
        raise ViewerDataError(f"record {record_index}: invalid search_goal_id")
    if not isinstance(search_goal, str):
        raise ViewerDataError(f"record {record_index}: search_goal must be a string")
    if not isinstance(web, Mapping):
        raise ViewerDataError(f"record {record_index}: web must be an object")

    has_sentence_ids = "sentence_ids" in record
    has_sentence_ranges = "sentence_ranges" in record
    if has_sentence_ids and has_sentence_ranges:
        raise ViewerDataError(
            f"record {record_index}: sentence_ids and sentence_ranges cannot both be present"
        )
    sentences = _list(record.get("sentences"), "sentences", record_index)
    selected_ids = _list(record.get("sentence_ids"), "sentence_ids", record_index)
    sentence_ranges = _list(record.get("sentence_ranges"), "sentence_ranges", record_index)
    quotes = _list(record.get("quotes"), "quotes", record_index)
    normalized_sentences = []
    for position, raw_sentence in enumerate(sentences, start=1):
        if not isinstance(raw_sentence, Mapping):
            raise ViewerDataError(
                f"record {record_index}: sentences[{position - 1}] must be an object"
            )
        sentence_id = raw_sentence.get("sentence_id")
        sentence = raw_sentence.get("sentence")
        if isinstance(sentence_id, bool) or not isinstance(sentence_id, int) or sentence_id < 1:
            raise ViewerDataError(
                f"record {record_index}: sentences[{position - 1}].sentence_id is invalid"
            )
        if not isinstance(sentence, str):
            raise ViewerDataError(
                f"record {record_index}: sentences[{position - 1}].sentence must be a string"
            )
        normalized_sentences.append({"sentence_id": sentence_id, "sentence": sentence})
    if any(isinstance(item, bool) or not isinstance(item, int) for item in selected_ids):
        raise ViewerDataError(f"record {record_index}: sentence_ids must contain integers")
    normalized_ranges = []
    previous_end = None
    for position, item in enumerate(sentence_ranges):
        if not isinstance(item, list) or len(item) != 2:
            raise ViewerDataError(
                f"record {record_index}: sentence_ranges[{position}] must be [start, end]"
            )
        start, end = item
        if any(isinstance(value, bool) or not isinstance(value, int) for value in item):
            raise ViewerDataError(
                f"record {record_index}: sentence_ranges[{position}] must contain integers"
            )
        if start < 1 or start > end or end > len(normalized_sentences):
            raise ViewerDataError(
                f"record {record_index}: sentence_ranges[{position}] has invalid bounds"
            )
        if previous_end is not None and start <= previous_end + 1:
            raise ViewerDataError(
                f"record {record_index}: sentence_ranges must be ordered and non-adjacent"
            )
        normalized_ranges.append([start, end])
        previous_end = end
    if any(not isinstance(item, str) for item in quotes):
        raise ViewerDataError(f"record {record_index}: quotes must contain strings")

    return {
        "record_index": record_index,
        "search_goal_id": str(goal_id),
        "search_goal": search_goal,
        "web": deepcopy(dict(web)),
        "sentences": normalized_sentences,
        "sentence_ids": selected_ids,
        "sentence_ranges": normalized_ranges,
        "selection_mode": (
            "sentence_ranges" if has_sentence_ranges
            else "sentence_ids" if has_sentence_ids
            else "verbatim"
        ),
        "quotes": quotes,
    }


def _selected_sentence_count(record: Mapping[str, Any]) -> int:
    selected = set(record["sentence_ids"])
    for start, end in record["sentence_ranges"]:
        selected.update(range(start, end + 1))
    return len(selected)


def load_view_data(
    quotes_path: str | Path,
    *,
    search_goals_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load, validate, group and summarize one extraction JSONL file."""
    source = Path(quotes_path).expanduser().resolve()
    records = [_validate_record(record) for record in _read_jsonl(source)]
    user_query, companion, warnings = _resolve_user_query(source, search_goals_path)

    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for record in records:
        goal_id = record["search_goal_id"]
        group = grouped.get(goal_id)
        if group is None:
            group = {
                "search_goal_id": goal_id,
                "search_goal": record["search_goal"],
                "records": [],
            }
            grouped[goal_id] = group
        elif group["search_goal"] != record["search_goal"]:
            raise ViewerDataError(
                f"search_goal_id {goal_id!r} has inconsistent search_goal text"
            )
        group["records"].append(record)

    goals = []
    for group in grouped.values():
        goal_records = group["records"]
        group["web_count"] = len(goal_records)
        group["sentence_count"] = sum(len(item["sentences"]) for item in goal_records)
        group["selected_sentence_count"] = sum(
            _selected_sentence_count(item) for item in goal_records
        )
        group["range_count"] = sum(len(item["sentence_ranges"]) for item in goal_records)
        group["uses_sentence_ranges"] = any(
            item["selection_mode"] == "sentence_ranges" for item in goal_records
        )
        group["quote_count"] = sum(len(item["quotes"]) for item in goal_records)
        goals.append(group)

    return {
        "user_query": user_query,
        "source_file": str(source),
        "source_name": source.name,
        "search_goals_file": str(companion) if companion else None,
        "warnings": warnings,
        "summary": {
            "goal_count": len(goals),
            "web_count": len(records),
            "sentence_count": sum(len(item["sentences"]) for item in records),
            "selected_sentence_count": sum(_selected_sentence_count(item) for item in records),
            "range_count": sum(len(item["sentence_ranges"]) for item in records),
            "quote_count": sum(len(item["quotes"]) for item in records),
            "empty_quote_web_count": sum(not item["quotes"] for item in records),
        },
        "goals": goals,
    }


def scan_quotes_files(quotes_dir: str | Path = DEFAULT_QUOTES_DIR) -> list[Path]:
    """Return JSONL result files found directly under ``quotes_dir``.

    Results are ordered by modification time (newest first), then by filename.
    Subdirectories and non-JSONL files are ignored.
    """
    directory = Path(quotes_dir).expanduser().resolve()
    if not directory.exists():
        raise ViewerDataError(f"quotes directory does not exist: {directory}")
    if not directory.is_dir():
        raise ViewerDataError(f"quotes path is not a directory: {directory}")
    paths = [
        path.resolve()
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() == ".jsonl"
    ]
    return sorted(paths, key=lambda path: (-path.stat().st_mtime_ns, path.name))


def _catalog_entry(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "size_bytes": stat.st_size,
        "modified_at_ms": stat.st_mtime_ns // 1_000_000,
    }


def _resolve_catalog_selection(
    paths: list[Path],
    quotes_dir: Path,
    selected_file: str | Path | None,
) -> Path | None:
    if not paths:
        if selected_file is not None:
            raise ViewerDataError(f"quotes directory contains no JSONL files: {quotes_dir}")
        return None
    if selected_file is None:
        return paths[0]

    requested = Path(selected_file).expanduser()
    candidates = [requested.resolve()]
    if not requested.is_absolute():
        candidates.append((quotes_dir / requested).resolve())
    available = {path: path for path in paths}
    for candidate in candidates:
        if candidate in available:
            return available[candidate]
    raise ViewerDataError(
        f"selected file is not a scanned JSONL file under {quotes_dir}: {selected_file}"
    )


class ViewerRequestHandler(BaseHTTPRequestHandler):
    """Serve scanned extraction files and bundled frontend assets."""

    server_version = "QuoteViewer/1.0"
    static_data: dict[str, Any] | None = None
    initial_data: dict[str, Any] | None = None
    quote_files: dict[str, Path] = {}
    file_entries: list[dict[str, Any]] = []
    selected_file: str | None = None
    search_goals_override: Path | None = None
    asset_dir = VIEWER_DIR

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _send_json(self, status: int, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send(status, payload, "application/json; charset=utf-8")

    def _requested_file(self, query: str) -> str | None:
        values = parse_qs(query, keep_blank_values=True).get("file")
        return values[0] if values else self.selected_file

    def _load_requested_data(self, file_name: str | None) -> dict[str, Any]:
        if file_name is None:
            raise ViewerDataError("quotes directory contains no JSONL files")
        if self.static_data is not None:
            if file_name != self.selected_file:
                raise KeyError(file_name)
            return self.static_data
        path = self.quote_files.get(file_name)
        if path is None:
            raise KeyError(file_name)
        if file_name == self.selected_file and self.initial_data is not None:
            return self.initial_data
        companion = self.search_goals_override if file_name == self.selected_file else None
        return load_view_data(path, search_goals_path=companion)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        path = unquote(request.path)
        if path == "/api/files":
            self._send_json(
                200,
                {
                    "files": self.file_entries,
                    "selected_file": self.selected_file,
                },
            )
            return
        if path == "/api/data":
            file_name = self._requested_file(request.query)
            try:
                data = self._load_requested_data(file_name)
            except KeyError:
                self._send_json(404, {"error": "所选文件不在启动时扫描的文件列表中。"})
            except (OSError, ViewerDataError) as exc:
                self._send_json(422, {"error": str(exc)})
            else:
                self._send_json(200, data)
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        asset = assets.get(path)
        if asset is None:
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        filename, content_type = asset
        try:
            payload = (self.asset_dir / filename).read_bytes()
        except OSError:
            self._send(500, b"Viewer asset unavailable", "text/plain; charset=utf-8")
            return
        self._send(200, payload, content_type)


def create_server(
    data: dict[str, Any] | None = None,
    *,
    quotes_dir: str | Path | None = None,
    selected_file: str | Path | None = None,
    search_goals_path: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create a local viewer server in static-data or scanned-directory mode."""
    if not isinstance(host, str) or not host:
        raise ValueError("host must be a non-empty string")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 to 65535")

    if data is not None and any(
        value is not None for value in (quotes_dir, selected_file, search_goals_path)
    ):
        raise ValueError("static data cannot be combined with directory scanning options")

    if data is not None:
        source_name = str(data.get("source_name") or "loaded-result.jsonl")
        entries = [{"name": source_name, "size_bytes": None, "modified_at_ms": None}]
        selected_name = source_name
        quote_files: dict[str, Path] = {}
        static_data = deepcopy(data)
        initial_data = None
        companion_override = None
        scanned_directory = None
    else:
        directory = Path(quotes_dir or DEFAULT_QUOTES_DIR).expanduser().resolve()
        paths = scan_quotes_files(directory)
        selected_path = _resolve_catalog_selection(paths, directory, selected_file)
        companion_override = (
            Path(search_goals_path).expanduser().resolve()
            if search_goals_path is not None
            else None
        )
        if companion_override is not None and selected_path is None:
            raise ViewerDataError("--search-goals requires a selected quotes file")
        initial_data = (
            load_view_data(selected_path, search_goals_path=companion_override)
            if selected_path is not None
            else None
        )
        entries = [_catalog_entry(path) for path in paths]
        selected_name = selected_path.name if selected_path is not None else None
        quote_files = {path.name: path for path in paths}
        static_data = None
        scanned_directory = directory

    handler = type(
        "BoundViewerRequestHandler",
        (ViewerRequestHandler,),
        {
            "static_data": static_data,
            "initial_data": initial_data,
            "quote_files": quote_files,
            "file_entries": entries,
            "selected_file": selected_name,
            "search_goals_override": companion_override,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.viewer_file_count = len(entries)  # type: ignore[attr-defined]
    server.viewer_selected_file = selected_name  # type: ignore[attr-defined]
    server.viewer_quotes_dir = scanned_directory  # type: ignore[attr-defined]
    return server
