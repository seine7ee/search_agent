"""Start the local quote-extraction result viewer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traj_web_extractor.visualizer import (
    DEFAULT_QUOTES_DIR,
    DEFAULT_QUOTES_FILE,
    ViewerDataError,
    create_server,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="可视化查看按 search goal、web 抽取的句子与 quotes")
    parser.add_argument(
        "input", nargs="?", type=Path,
        help="启动后默认选中的 JSONL；必须位于扫描目录内",
    )
    parser.add_argument(
        "--quotes-dir", type=Path, default=DEFAULT_QUOTES_DIR,
        help="自动扫描的 quotes JSONL 目录",
    )
    parser.add_argument(
        "--search-goals", type=Path,
        help="可选的配套 search_goals JSON；默认根据运行标识自动查找",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    try:
        quotes_dir = args.quotes_dir.expanduser().resolve()
        selected_file = args.input
        if (
            selected_file is None
            and quotes_dir == DEFAULT_QUOTES_DIR.resolve()
            and DEFAULT_QUOTES_FILE.is_file()
        ):
            selected_file = DEFAULT_QUOTES_FILE
        server = create_server(
            quotes_dir=quotes_dir,
            selected_file=selected_file,
            search_goals_path=args.search_goals,
            host=args.host,
            port=args.port,
        )
    except (OSError, ValueError, ViewerDataError) as exc:
        parser.exit(1, f"可视化启动失败：{exc}\n")

    actual_host, actual_port = server.server_address[:2]
    display_host = "127.0.0.1" if actual_host in ("0.0.0.0", "::") else actual_host
    print(f"扫描目录：{server.viewer_quotes_dir}")
    print(f"发现文件：{server.viewer_file_count}")
    print(f"默认文件：{server.viewer_selected_file or '无'}")
    print(f"页面地址：http://{display_host}:{actual_port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n可视化服务已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
