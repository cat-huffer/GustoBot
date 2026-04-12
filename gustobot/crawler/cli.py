"""
爬虫命令行入口：从外部数据源取数，映射为菜谱 JSON，可选 POST 至知识库批量接口。

当前子命令 ``wikipedia``：经 MediaWiki API 取维基**导语摘要**（非整页 HTML），用于联调/演示检索。
实际入库依赖已启动的后端与 Embedding/Milvus 配置，详见 ``docs/爬虫与批量导入指南.md``。

示例：

  python -m gustobot.crawler.cli wikipedia --query "川菜" --import-kb --limit 10
"""


import argparse
import json
import sys
from typing import Any, Dict, List, Optional

import httpx

from .wikipedia import fetch_wikipedia_pages, wikipedia_page_to_recipe


def _post_recipes_batch(
    *,
    api_base_url: str,
    recipes: List[Dict[str, Any]],
    timeout: float,
) -> Dict[str, Any]:
    """调用 ``POST .../recipes/batch`` 写入一批菜谱，返回解析后的 JSON 响应。"""
    url = api_base_url.rstrip("/") + "/api/v1/knowledge/recipes/batch"
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.post(url, json=recipes)
        response.raise_for_status()
        return response.json() if response.content else {}


def _build_parser() -> argparse.ArgumentParser:
    """构建带子命令的解析器（当前仅 ``wikipedia``）。"""
    parser = argparse.ArgumentParser(
        prog="python -m gustobot.crawler.cli",
        description="外部数据抓取并导入知识库，或仅输出/落盘 JSON（不连后端）。",
    )
    subparsers = parser.add_subparsers(dest="source", required=True)

    wikipedia = subparsers.add_parser(
        "wikipedia",
        help="按关键词搜索维基并取页面摘要，映射为菜谱形数据",
    )
    wikipedia.add_argument("--query", required=True, help="搜索关键词")
    wikipedia.add_argument("--limit", type=int, default=10, help="最多抓取页面条数")
    wikipedia.add_argument("--lang", default="zh", help="语言子域，如 zh → zh.wikipedia.org（默认：zh）")
    wikipedia.add_argument("--import-kb", action="store_true", help="通过 HTTP 批量接口写入知识库")
    wikipedia.add_argument(
        "--api-base-url",
        default="http://localhost:8000",
        help="后端根 URL（默认：http://localhost:8000）",
    )
    wikipedia.add_argument("--timeout", type=float, default=30.0, help="HTTP 超时（秒）")
    wikipedia.add_argument("--output", default=None, help="将原始页面列表写入该 JSON 路径")
    wikipedia.add_argument("--dry-run", action="store_true", help="不 POST，仅打印转换后的菜谱 JSON")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """解析参数：拉取维基 → 转菜谱列表 → dry-run/仅输出 或 调用批量接口导入。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.source == "wikipedia":
        try:
            pages = fetch_wikipedia_pages(
                args.query, limit=args.limit, lang=args.lang, timeout=args.timeout
            )
        except httpx.HTTPError as exc:
            print(f"Failed to fetch Wikipedia pages: {exc}", file=sys.stderr)
            return 1

        if args.output:
            with open(args.output, "w", encoding="utf-8") as fp:
                json.dump(pages, fp, ensure_ascii=False, indent=2)

        recipes = [wikipedia_page_to_recipe(page, query=args.query) for page in pages]

        if args.dry_run or not args.import_kb:
            print(json.dumps(recipes, ensure_ascii=False, indent=2))
            return 0

        try:
            result = _post_recipes_batch(
                api_base_url=args.api_base_url, recipes=recipes, timeout=args.timeout
            )
        except httpx.HTTPError as exc:
            print(f"Failed to import into KB API: {exc}", file=sys.stderr)
            return 1

        inserted = result.get("statistics", {}).get("success")
        print(f"Imported {inserted if inserted is not None else len(recipes)} items into KB.")
        return 0

    parser.error(f"Unknown source: {args.source}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
