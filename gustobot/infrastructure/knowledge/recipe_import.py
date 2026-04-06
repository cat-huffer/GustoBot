"""
将「菜谱形态」的原始数据整理成知识库 API 可用的字典。

本模块保持**纯函数**（不连 Milvus、不调 LLM），便于离线单测与脚本导入。
"""

import re
from typing import Any, Dict, List, Optional


_STEP_NUMBER_SPLIT_RE = re.compile(r"\s*\d+\s*[:：]\s*")


def _normalize_ingredient_pairs(value: Any) -> List[str]:
    """把各种常见「食材」字段形态统一成可读字符串列表。

    支持的输入形态示例：

    - ``[["香肠", "2根"], ["菜干", "200g"]]``：二元组列表，拼成 ``"名称 用量"``。
    - ``{"香肠": "2根", "菜干": "200g"}``：字典，键为食材名、值为用量。
    - ``["盐", "少许"]``：纯字符串列表，非二元组元素整段作为一条。
    - 其它标量：转成字符串后若非空则作为单元素列表。

    Args:
        value: 来自 JSON 的 ``主食材`` / ``辅料`` 等字段，类型不固定。

    Returns:
        去空后的食材描述列表；``None`` 或无法解析时返回 ``[]``。
    """

    if value is None:
        return []

    ingredients: List[str] = []

    if isinstance(value, dict):
        for name, amount in value.items():
            name_str = str(name).strip()
            amount_str = str(amount).strip() if amount is not None else ""
            if not name_str:
                continue
            ingredients.append(f"{name_str} {amount_str}".strip())
        return ingredients

    if isinstance(value, (list, tuple)):
        for item in value:
            if item is None:
                continue
            if isinstance(item, (list, tuple)):
                if not item:
                    continue
                name = str(item[0]).strip()
                amount = str(item[1]).strip() if len(item) > 1 and item[1] is not None else ""
                if name:
                    ingredients.append(f"{name} {amount}".strip())
                continue
            item_str = str(item).strip()
            if item_str:
                ingredients.append(item_str)
        return ingredients

    value_str = str(value).strip()
    return [value_str] if value_str else []


def split_steps(text: Optional[str]) -> List[str]:
    """把一条「做法」长文本拆成步骤列表。

    优先级：

    1. 若含 ``1：`` / ``2:`` 这类序号前缀，按正则切分并去掉空段。
    2. 否则若含换行，按行切分。
    3. 否则按中文句号、分号（及英文 ``;``）等句子分隔符切分。

    Args:
        text: 原始做法字符串；``None``、空串或纯空白返回 ``[]``。

    Returns:
        非空的步骤字符串列表，顺序与原文大致一致。
    """

    if not text:
        return []

    normalized = str(text).strip().replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return []

    if _STEP_NUMBER_SPLIT_RE.search(normalized):
        parts = [part.strip() for part in _STEP_NUMBER_SPLIT_RE.split(normalized) if part.strip()]
        return parts

    if "\n" in normalized:
        return [line.strip() for line in normalized.split("\n") if line.strip()]

    # Fallback: split by common sentence separators.
    parts = [part.strip() for part in re.split(r"[。；;]\s*", normalized) if part.strip()]
    return parts


def recipe_json_entry_to_recipe(name: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """将 ``data/recipe.json`` 风格的一条记录映射为知识库菜谱载荷。

    约定字段（中文键）：``主食材``、``辅料``、``做法``、``类型``、``耗时``、``口味``、
    ``工艺`` 等。``主食材`` 与 ``辅料`` 经 :func:`_normalize_ingredient_pairs` 合并为
    ``ingredients``；``做法`` 经 :func:`split_steps` 得到 ``steps``；``口味`` 与
    ``工艺`` 拼进 ``tips``。

    Args:
        name: 菜谱名称（作为导出载荷的 ``name``）。
        entry: 单条菜谱对象，键为上述中文字段。

    Returns:
        供 ``KnowledgeService.add_recipe`` 等使用的字典，键包括 ``name``、``category``、
        ``time``、``ingredients``、``steps``、``tips``；列表类字段在无内容时为 ``None``。
    """

    main_ingredients = _normalize_ingredient_pairs(entry.get("主食材"))
    extra_ingredients = _normalize_ingredient_pairs(entry.get("辅料"))
    ingredients = [*main_ingredients, *extra_ingredients]

    steps = split_steps(entry.get("做法"))

    tips_parts: List[str] = []
    taste = entry.get("口味")
    if taste:
        tips_parts.append(f"口味：{taste}")
    craft = entry.get("工艺")
    if craft:
        tips_parts.append(f"工艺：{craft}")
    tips = "；".join(tips_parts) if tips_parts else None

    payload: Dict[str, Any] = {
        "name": name,
        "category": entry.get("类型") or None,
        "time": entry.get("耗时") or None,
        "ingredients": ingredients or None,
        "steps": steps or None,
        "tips": tips,
    }
    return payload
