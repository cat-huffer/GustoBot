"""
维基百科数据源：通过公开 MediaWiki HTTP API 完成「搜索标题 → 拉取导语级纯文本与规范 URL」。

**职责**：仅网络请求与结构化字段；**不**直连向量库。入库由 ``gustobot.crawler.cli`` 调后端
``/api/v1/knowledge/recipes/batch`` 完成。

**可测性**：``wikipedia_page_to_recipe`` 纯映射，可在不联网下单测；搜索/抓取函数与 httpx 耦合。

**网络**：请求使用 ``trust_env=False``（默认不走系统代理），与项目批量导入文档说明一致。
"""


from typing import Any, Dict, List, Optional

import httpx


def wikipedia_page_to_recipe(page: Dict[str, Any], *, query: Optional[str] = None) -> Dict[str, Any]:
    """
    将单条维基页面（title / extract / fullurl）转为知识库 API 可接受的菜谱形字典。

    ``extract`` 整段作为 ``steps`` 单列，便于向量化检索；``tips`` 附来源链接；``ingredients`` 一般为
    ``None``。适用于联调与演示，**非**标准烹饪步骤数据。
    """

    title = (page.get("title") or page.get("name") or "").strip()
    extract = (page.get("extract") or page.get("content") or "").strip()
    fullurl = (page.get("fullurl") or page.get("url") or "").strip()

    category = "Wikipedia"
    if query:
        category = f"Wikipedia/{query}"

    tips_parts: List[str] = []
    if fullurl:
        tips_parts.append(f"来源：{fullurl}")

    payload: Dict[str, Any] = {
        "name": title or (query or "Wikipedia"),
        "category": category,
        "ingredients": None,
        "steps": [extract] if extract else None,
        "tips": "；".join(tips_parts) if tips_parts else None,
    }
    return payload


def _api_url(lang: str) -> str:
    """返回 ``https://{lang}.wikipedia.org/w/api.php``。"""
    return f"https://{lang}.wikipedia.org/w/api.php"


def search_wikipedia_titles(
    query: str,
    *,
    limit: int = 10,
    lang: str = "zh",
    timeout: float = 20.0,
) -> List[str]:
    """
    调用 ``list=search``，返回与关键词匹配的页面标题列表。

    ``srlimit`` 受 ``limit`` 与 API 上限（约 50）共同约束；空查询返回空列表。
    """

    if not query or not query.strip():
        return []

    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": max(1, min(int(limit), 50)),
        "format": "json",
    }

    response = httpx.get(_api_url(lang), params=params, timeout=timeout, trust_env=False)
    response.raise_for_status()
    payload = response.json()

    results = payload.get("query", {}).get("search", []) or []
    titles = [item.get("title") for item in results if isinstance(item, dict) and item.get("title")]
    return [str(t) for t in titles]


def fetch_wikipedia_pages(
    query: str,
    *,
    limit: int = 10,
    lang: str = "zh",
    timeout: float = 20.0,
) -> List[Dict[str, Any]]:
    """
    对搜索关键词：先取标题列表，再一次性请求 ``prop=extracts|info`` 拉取导语与 ``fullurl``。

    返回字典列表，每项含 ``title``、``extract``、``fullurl``；顺序与搜索命中顺序一致（便于 CLI 稳定输出）。
    无命中时返回空列表。
    """

    titles = search_wikipedia_titles(query, limit=limit, lang=lang, timeout=timeout)
    if not titles:
        return []

    # Fetch extracts + canonical urls in one request.
    params = {
        "action": "query",
        "prop": "extracts|info",
        "explaintext": 1,
        "exintro": 1,
        "inprop": "url",
        "titles": "|".join(titles),
        "format": "json",
    }

    response = httpx.get(_api_url(lang), params=params, timeout=timeout, trust_env=False)
    response.raise_for_status()
    payload = response.json()

    pages = payload.get("query", {}).get("pages", {}) or {}
    collected: List[Dict[str, Any]] = []
    for _page_id, page in pages.items():
        if not isinstance(page, dict):
            continue
        title = page.get("title")
        if not title:
            continue
        collected.append(
            {
                "title": str(title),
                "extract": (page.get("extract") or "").strip(),
                "fullurl": (page.get("fullurl") or "").strip(),
            }
        )

    # Keep order stable (search order).
    by_title = {item["title"]: item for item in collected if item.get("title")}
    ordered = [by_title[title] for title in titles if title in by_title]
    # Append any leftover pages (unlikely, but safe)
    leftover = [item for item in collected if item.get("title") not in set(titles)]
    return ordered + leftover
