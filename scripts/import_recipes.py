#!/usr/bin/env python3
"""
通过 HTTP 接口批量将菜谱导入知识库。

示例：
  python scripts/import_recipes.py --file data/recipe.json --batch-size 100
"""


import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gustobot.infrastructure.knowledge.recipe_import import recipe_json_entry_to_recipe


def _iter_payloads_from_json(
    raw: Any,
    *,
    limit: Optional[int] = None,
) -> Iterator[Dict[str, Any]]:
    """
    从 JSON 根对象迭代产出知识库可用的菜谱字典。

    支持：
    - 以菜名为键、菜谱子对象为值的字典（与仓库内 data/recipe.json 风格一致）
    - 菜谱字典组成的列表（已接近 API 字段或含「主食材/辅料/做法」等中文键时做映射）
    """

    emitted = 0

    if isinstance(raw, dict):
        for name, entry in raw.items():
            if limit is not None and emitted >= limit:
                return
            if not isinstance(entry, dict):
                continue
            emitted += 1
            yield recipe_json_entry_to_recipe(str(name), entry)
        return

    if isinstance(raw, list):
        for item in raw:
            if limit is not None and emitted >= limit:
                return
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("title") or item.get("菜名")
            if not name:
                continue
            emitted += 1
            # If already matches API fields, keep as-is, otherwise attempt a light mapping.
            if "主食材" in item or "辅料" in item or "做法" in item:
                yield recipe_json_entry_to_recipe(str(name), item)
            else:
                payload = {
                    "name": str(name),
                    "category": item.get("category") or item.get("类型"),
                    "time": item.get("time") or item.get("耗时"),
                    "ingredients": item.get("ingredients"),
                    "steps": item.get("steps"),
                    "tips": item.get("tips"),
                }
                yield payload
        return

    raise TypeError(f"Unsupported JSON root type: {type(raw)!r}")


def _chunked(iterable: Iterable[Dict[str, Any]], size: int) -> Iterator[List[Dict[str, Any]]]:
    """将可迭代对象按固定条数切成批次。"""
    batch: List[Dict[str, Any]] = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _post_batch(
    client: httpx.Client,
    *,
    api_base_url: str,
    recipes: List[Dict[str, Any]],
) -> Tuple[int, Dict[str, Any]]:
    """POST 一批菜谱到批量接口，返回成功写入条数与响应 JSON。"""
    url = api_base_url.rstrip("/") + "/api/v1/knowledge/recipes/batch"
    response = client.post(url, json=recipes)
    response.raise_for_status()
    payload = response.json() if response.content else {}
    inserted = int(payload.get("statistics", {}).get("success", len(recipes)))
    return inserted, payload


def main(argv: Optional[List[str]] = None) -> int:
    """解析命令行参数，执行 dry-run 或分批 POST 导入。"""
    parser = argparse.ArgumentParser(description="通过 HTTP 批量将菜谱导入知识库")
    parser.add_argument("--file", required=True, help="菜谱 JSON 文件路径")
    parser.add_argument("--batch-size", type=int, default=100, help="每请求包含的菜谱条数")
    parser.add_argument(
        "--api-base-url",
        default="http://localhost:8000",
        help="后端根 URL（默认：http://localhost:8000）",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅导入前 N 条菜谱")
    parser.add_argument("--dry-run", action="store_true", help="仅转换并打印样例，不发起 POST")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP 超时（秒）")
    args = parser.parse_args(argv)

    json_path = Path(args.file)
    if not json_path.exists():
        print(f"File not found: {json_path}", file=sys.stderr)
        return 2

    with json_path.open("r", encoding="utf-8") as fp:
        raw = json.load(fp)

    payload_iter = _iter_payloads_from_json(raw, limit=args.limit)

    if args.dry_run:
        sample = []
        for idx, item in enumerate(payload_iter):
            if idx >= min(args.batch_size, 5):
                break
            sample.append(item)
        print(json.dumps(sample, ensure_ascii=False, indent=2))
        return 0

    total = 0
    started = time.time()

    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        for batch_idx, batch in enumerate(_chunked(payload_iter, args.batch_size), start=1):
            inserted, _payload = _post_batch(client, api_base_url=args.api_base_url, recipes=batch)
            total += inserted
            elapsed = time.time() - started
            rate = total / elapsed if elapsed > 0 else 0.0
            print(
                f"[batch {batch_idx}] sent={len(batch)} inserted={inserted} total={total} rate={rate:.1f}/s"
            )

    print(f"Done. Imported {total} recipes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
