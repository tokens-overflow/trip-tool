"""LLM 用例：地点证据 → 结构化 JSON 多日行程。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..core.llm_task import JsonLLMTask
from ..models import Place, TaskNode
from ..prompts import itinerary_messages


class ItineraryInput(BaseModel):
    topic: str
    tasks: list[TaskNode]
    weather: str = ""        # 天气预报文本块（可空）
    trip_context: str = ""   # 出行日期/预算等上下文（可空）

    model_config = {"arbitrary_types_allowed": True}


# 返回值：每日行程数组
ItineraryResult = list[dict[str, Any]]


class ItineraryTask(JsonLLMTask[ItineraryInput, ItineraryResult]):
    """生成多日行程。LLM 失败时由 ComposeStage 兜底为空行程。"""

    def build_messages(self, input: ItineraryInput) -> list[dict[str, str]]:
        evidence_text, _places = evidence_for_itinerary(input.tasks)
        return itinerary_messages(
            topic=input.topic,
            evidence_block=evidence_text,
            weather=input.weather,
            trip_context=input.trip_context,
        )

    def parse(self, raw: Any, input: ItineraryInput) -> ItineraryResult:
        return _extract_days(raw)


# ---------------------------------------------------------------------------
# 辅助函数 —— 纯字符串 / 几何处理，不调 LLM
# （公开导出：ComposeStage 也用它取去重后的地点列表来定位天气锚点）
# ---------------------------------------------------------------------------
def evidence_for_itinerary(tasks: list[TaskNode]) -> tuple[str, list[Place]]:
    """按 place_id 去重所有任务证据中的地点，并格式化成 Prompt 用的文本。"""
    seen: set[str] = set()
    places: list[Place] = []
    for task in tasks:
        for place in task.evidence.places:
            if place.place_id and place.place_id in seen:
                continue
            seen.add(place.place_id)
            places.append(place)

    lines: list[str] = []
    for idx, place in enumerate(places, start=1):
        price = f" 价位={'¥' * place.price_level}" if place.price_level else ""
        hours = (" 营业=" + " | ".join(place.opening_hours)) if place.opening_hours else ""
        lines.append(
            f"{idx}. place_id={place.place_id} name={place.name} "
            f"rating={place.rating or '-'}{price} address={place.address}{hours}"
        )
    return "\n".join(lines) or "（无地点）", places


def center_of(places: list[Place]) -> dict[str, float] | None:
    """所有地点经纬度的几何中心，用作天气查询的锚点（无有效坐标返回 None）。"""
    lats = [p.lat for p in places if p.lat]
    lngs = [p.lng for p in places if p.lng]
    if not lats or not lngs:
        return None
    return {"lat": sum(lats) / len(lats), "lng": sum(lngs) / len(lngs)}


def _extract_days(raw: Any) -> list[dict[str, Any]]:
    """容忍 LLM 返回 ``[...]`` 或 ``{"days": [...]}`` / ``{"itinerary": [...]}`` 等包装。

    只保留带 ``slots`` 字段的 dict —— 没有 slots 的"天"前端渲染不了。
    """
    if isinstance(raw, dict):
        for key in ("days", "itinerary", "schedule"):
            value = raw.get(key)
            if isinstance(value, list):
                raw = value
                break
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict) and "slots" in item]
    return []
