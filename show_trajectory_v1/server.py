#!/usr/bin/env python3
"""Local, dependency-free viewer for deep_search_single_agent / deep_search_multi_agent trajectories.

Usage:
    python3 show_trajectory_v1/server.py [--port 8766] [--path <traj json>] [--no-browser]

Then open the printed URL (or let it auto-open) and type/pick the path to a
``traj_*.json`` file produced by ``deep_search_single_agent`` or
``deep_search_multi_agent``. Everything is read-only: the server only reads
files from disk and never executes or writes anything.

This is a copy of ``show_trajectory/server.py`` with the recent-trajectory
search directories retargeted at the two newer schemes; the server itself
(path resolution, static file serving, JSON loading) is schema-agnostic and
unchanged.

This binds to 127.0.0.1 only — it is a local dev tool, not a service meant
to be reachable from the network — and it will happily read whatever path
you give it, which is the whole point (there is no sandboxing beyond that).
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}

# Top-level directories (relative to the repo root) scanned recursively for
# traj_*.json files to power the "recent trajectories" quick-pick list.
TRAJECTORY_SEARCH_DIRS = [
    "deep_search_single_agent",
    "deep_search_multi_agent",
    "batch_trajectories",
]


def _resolve_path(raw: str) -> Path:
    """Resolve a user-typed path against cwd first, then the repo root."""
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    cwd_candidate = (Path.cwd() / candidate).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    root_candidate = (REPO_ROOT / candidate).resolve()
    if root_candidate.exists():
        return root_candidate
    return cwd_candidate  # doesn't exist under either base; report against this one


def _relative_label(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _discover_trajectories() -> list[dict]:
    found: list[dict] = []
    seen: set[Path] = set()
    for rel_dir in TRAJECTORY_SEARCH_DIRS:
        base = REPO_ROOT / rel_dir
        if not base.is_dir():
            continue
        for path in base.rglob("traj_*.json"):
            resolved = path.resolve()
            if resolved in seen or not resolved.is_file():
                continue
            seen.add(resolved)
            try:
                stat = resolved.stat()
            except OSError:
                continue
            found.append(
                {
                    "path": _relative_label(resolved),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
    found.sort(key=lambda item: item["mtime"], reverse=True)
    return found[:300]


class Handler(BaseHTTPRequestHandler):
    server_version = "TrajectoryViewerV1/1.0"

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urlparse(self.path)
        if parsed.path in STATIC_FILES:
            self._serve_static(parsed.path)
        elif parsed.path == "/api/list":
            self._send_json({"trajectories": _discover_trajectories()})
        elif parsed.path == "/api/load":
            query = parse_qs(parsed.query)
            raw_path = (query.get("path") or [""])[0].strip()
            self._serve_trajectory(raw_path)
        else:
            self._send_json({"error": f"未找到: {parsed.path}"}, status=404)

    def _serve_static(self, url_path: str) -> None:
        filename, content_type = STATIC_FILES[url_path]
        file_path = STATIC_DIR / filename
        try:
            body = file_path.read_bytes()
        except OSError as exc:
            self._send_json({"error": str(exc)}, status=500)
            return
        self._send_bytes(body, content_type)

    def _serve_trajectory(self, raw_path: str) -> None:
        if not raw_path:
            self._send_json({"error": "path 参数不能为空"}, status=400)
            return
        resolved = _resolve_path(raw_path)
        if not resolved.exists():
            self._send_json({"error": f"文件不存在: {resolved}"}, status=404)
            return
        if resolved.is_dir():
            self._send_json({"error": f"这是一个目录，不是文件: {resolved}"}, status=400)
            return
        try:
            text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            self._send_json({"error": f"读取文件失败: {exc}"}, status=500)
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            self._send_json({"error": f"JSON 解析失败: {exc}"}, status=400)
            return
        self._send_json({"resolved_path": _relative_label(resolved), "data": data})

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        print(f"[show_trajectory_v1] {self.address_string()} - {format % args}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="本地轨迹可视化查看器 v1（single_agent / multi_agent，只读，不引入额外依赖）"
    )
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--path", help="启动后自动加载该轨迹文件（相对或绝对路径）")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    if args.path:
        url += f"?path={quote(args.path)}"
    print(f"[show_trajectory_v1] serving at {url}")
    print("[show_trajectory_v1] Ctrl+C 停止")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
