"""结构化六边形地图模型与工具函数。"""

import copy
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

TERRAIN_TYPES = {
    "unset", "plain", "forest", "mountain", "desert", "snow", "ocean",
    "lake", "river", "swamp", "city", "ruins", "special",
}

STATUS_TYPES = {
    "normal", "war", "occupied", "disaster", "blocked", "abandoned", "unknown", "special",
}

MAP_TYPES = {"world", "continent", "country", "region", "city", "battlefield", "dungeon", "planet", "galaxy"}

LAYER_TYPES = ["terrain", "faction", "resource", "event", "status"]

HEX_DIRECTIONS: List[Tuple[int, int]] = [
    (1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)
]


def now_iso() -> str:
    return datetime.now().isoformat()


def map_id() -> str:
    return f"map_{uuid.uuid4().hex[:12]}"


def cell_id(q: int, r: int) -> str:
    return f"cell_{q}_{r}"


def change_id() -> str:
    return f"chg_{uuid.uuid4().hex[:12]}"


def clamp_map_size(value: Any, default: int, min_value: int = 3, max_value: int = 40) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(min_value, min(max_value, number))


def default_layers() -> List[Dict[str, Any]]:
    return [
        {"type": layer_type, "visible": layer_type == "terrain", "rules": {}, "field": layer_type}
        for layer_type in LAYER_TYPES
    ]


def normalize_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace("，", ",").split(",")]
        return [part for part in parts if part]
    return [value]


def normalize_cell(raw: Dict[str, Any], map_id_value: str, q: int = 0, r: int = 0) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    cq = int(raw.get("q", raw.get("x", q)) or 0)
    cr = int(raw.get("r", raw.get("y", r)) or 0)
    terrain = str(raw.get("terrain") or "unset").strip() or "unset"
    status = str(raw.get("status") or "normal").strip() or "normal"
    if terrain not in TERRAIN_TYPES:
        terrain = "special"
    if status not in STATUS_TYPES:
        status = "special"

    return {
        "id": raw.get("id") or cell_id(cq, cr),
        "map_id": raw.get("map_id") or map_id_value,
        "q": cq,
        "r": cr,
        "name": str(raw.get("name") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "terrain": terrain,
        "status": status,
        "faction": str(raw.get("faction") or raw.get("owner") or "").strip(),
        "resources": normalize_list(raw.get("resources")),
        "population": str(raw.get("population") or "").strip(),
        "settlement": str(raw.get("settlement") or "").strip(),
        "locations": normalize_list(raw.get("locations")),
        "entity_relations": [dict(item) for item in normalize_list(raw.get("entity_relations")) if isinstance(item, dict)],
        "event_relations": [dict(item) for item in normalize_list(raw.get("event_relations")) if isinstance(item, dict)],
        "tags": normalize_list(raw.get("tags")),
        "notes": str(raw.get("notes") or "").strip(),
        "custom": dict(raw.get("custom")) if isinstance(raw.get("custom"), dict) else {},
        "updated_at": raw.get("updated_at") or now_iso(),
    }


def create_cells(map_id_value: str, width: int, height: int) -> List[Dict[str, Any]]:
    return [normalize_cell({}, map_id_value, q=q, r=r) for r in range(height) for q in range(width)]


def normalize_map(raw: Dict[str, Any], world_id: str = "") -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    mid = raw.get("id") or map_id()
    width = clamp_map_size(raw.get("width"), 12)
    height = clamp_map_size(raw.get("height"), 8)
    raw_cells = raw.get("cells") if isinstance(raw.get("cells"), list) else []
    cells = [normalize_cell(cell, mid) for cell in raw_cells]
    if not cells:
        cells = create_cells(mid, width, height)

    map_type = str(raw.get("type") or "world").strip() or "world"
    if map_type not in MAP_TYPES:
        map_type = "region"

    return {
        "id": mid,
        "world_id": raw.get("world_id") or world_id,
        "name": str(raw.get("name") or "未命名地图").strip() or "未命名地图",
        "description": str(raw.get("description") or "").strip(),
        "type": map_type,
        "width": width,
        "height": height,
        "is_default": bool(raw.get("is_default", False)),
        "view": dict(raw.get("view")) if isinstance(raw.get("view"), dict) else {"scale": 1, "offset_x": 0, "offset_y": 0},
        "layers": raw.get("layers") if isinstance(raw.get("layers"), list) else default_layers(),
        "cells": cells,
        "change_records": [dict(item) for item in raw.get("change_records", []) if isinstance(item, dict)],
        "created_at": raw.get("created_at") or now_iso(),
        "updated_at": raw.get("updated_at") or now_iso(),
    }


def get_maps_container(world: Any) -> Dict[str, Any]:
    if not isinstance(world.settings, dict):
        world.settings = {}
    map_data = world.settings.setdefault("mapData", {})
    if not isinstance(map_data, dict):
        map_data = {}
        world.settings["mapData"] = map_data
    maps = map_data.setdefault("structuredMaps", [])
    if not isinstance(maps, list):
        maps = []
        map_data["structuredMaps"] = maps
    return map_data


def list_maps(world: Any) -> List[Dict[str, Any]]:
    map_data = get_maps_container(world)
    maps = [normalize_map(item, world.id) for item in map_data.get("structuredMaps", []) if isinstance(item, dict)]
    if maps and not any(item.get("is_default") for item in maps):
        maps[0]["is_default"] = True
    map_data["structuredMaps"] = maps
    return maps


def save_maps(world: Any, maps: List[Dict[str, Any]]) -> None:
    map_data = get_maps_container(world)
    if maps and not any(item.get("is_default") for item in maps):
        maps[0]["is_default"] = True
    map_data["structuredMaps"] = maps


def summarize_map(item: Dict[str, Any]) -> Dict[str, Any]:
    cells = item.get("cells") or []
    return {
        "id": item.get("id"),
        "world_id": item.get("world_id"),
        "name": item.get("name"),
        "description": item.get("description"),
        "type": item.get("type"),
        "width": item.get("width"),
        "height": item.get("height"),
        "is_default": bool(item.get("is_default")),
        "cell_count": len(cells),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def find_map(maps: List[Dict[str, Any]], target_map_id: str) -> Optional[Dict[str, Any]]:
    return next((item for item in maps if item.get("id") == target_map_id), None)


def find_cell(item: Dict[str, Any], target_cell_id: str) -> Optional[Dict[str, Any]]:
    return next((cell for cell in item.get("cells", []) if cell.get("id") == target_cell_id), None)


def cell_neighbors(item: Dict[str, Any], target_cell: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_coord = {(cell.get("q"), cell.get("r")): cell for cell in item.get("cells", [])}
    q = target_cell.get("q", 0)
    r = target_cell.get("r", 0)
    return [by_coord[(q + dq, r + dr)] for dq, dr in HEX_DIRECTIONS if (q + dq, r + dr) in by_coord]


def add_change_record(item: Dict[str, Any], object_type: str, object_id: str, before: Any, after: Any, source: str = "user", agent: bool = False) -> None:
    records = item.setdefault("change_records", [])
    records.append({
        "id": change_id(),
        "object_type": object_type,
        "object_id": object_id,
        "before": before,
        "after": after,
        "source": source,
        "is_agent": bool(agent),
        "created_at": now_iso(),
    })
    if len(records) > 300:
        del records[:-300]


def apply_cell_update(cell: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    updated = copy.deepcopy(cell)
    scalar_fields = [
        "name", "description", "terrain", "status", "faction", "population", "settlement", "notes"
    ]
    for field in scalar_fields:
        if field in updates:
            value = updates.get(field)
            if field == "terrain" and value not in TERRAIN_TYPES:
                value = "special" if value else "unset"
            if field == "status" and value not in STATUS_TYPES:
                value = "special" if value else "normal"
            updated[field] = str(value or "").strip()

    for field in ["resources", "locations", "tags"]:
        if field in updates:
            updated[field] = normalize_list(updates.get(field))

    for field in ["entity_relations", "event_relations"]:
        if field in updates:
            updated[field] = [dict(item) for item in normalize_list(updates.get(field)) if isinstance(item, dict)]

    if isinstance(updates.get("custom"), dict):
        updated["custom"] = {**updated.get("custom", {}), **updates["custom"]}

    updated["updated_at"] = now_iso()
    return updated


def apply_batch_update(cell: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    normalized_updates: Dict[str, Any] = {}
    for field in ["terrain", "status", "faction", "description", "notes"]:
        if field in updates:
            normalized_updates[field] = updates.get(field)
    updated = apply_cell_update(cell, normalized_updates)

    for field in ["resources", "tags", "locations"]:
        if field in updates:
            current = [] if updates.get(f"clear_{field}") else normalize_list(updated.get(field))
            for value in normalize_list(updates.get(field)):
                if value not in current:
                    current.append(value)
            updated[field] = current
        elif updates.get(f"clear_{field}"):
            updated[field] = []

    for field in ["faction", "status", "terrain"]:
        if updates.get(f"clear_{field}"):
            updated[field] = "" if field == "faction" else ("normal" if field == "status" else "unset")

    updated["updated_at"] = now_iso()
    return updated


def search_cells(item: Dict[str, Any], query: str = "") -> List[Dict[str, Any]]:
    q = str(query or "").strip().lower()
    results = []
    for cell in item.get("cells", []):
        haystack_parts = [
            cell.get("id"), cell.get("name"), cell.get("description"), cell.get("terrain"),
            cell.get("status"), cell.get("faction"), cell.get("population"), cell.get("settlement"),
            cell.get("notes"), " ".join(map(str, cell.get("resources") or [])),
            " ".join(map(str, cell.get("locations") or [])), " ".join(map(str, cell.get("tags") or [])),
        ]
        for rel in cell.get("entity_relations") or []:
            haystack_parts.extend([rel.get("entity_id"), rel.get("entity_name"), rel.get("entity_type"), rel.get("relation_type")])
        for rel in cell.get("event_relations") or []:
            haystack_parts.extend([rel.get("event_id"), rel.get("event_name"), rel.get("relation_type")])
        haystack = "\n".join(str(part or "") for part in haystack_parts).lower()
        if not q or q in haystack:
            results.append(cell)
    return results


def stats_for_map(item: Dict[str, Any]) -> Dict[str, Any]:
    stats = {
        "terrain": {},
        "status": {},
        "faction": {},
        "resources": {},
        "events": 0,
        "entities": 0,
    }
    for cell in item.get("cells", []):
        for key in ["terrain", "status", "faction"]:
            value = cell.get(key) or "未设置"
            stats[key][value] = stats[key].get(value, 0) + 1
        for resource in cell.get("resources") or []:
            stats["resources"][resource] = stats["resources"].get(resource, 0) + 1
        stats["events"] += len(cell.get("event_relations") or [])
        stats["entities"] += len(cell.get("entity_relations") or [])
    return stats
