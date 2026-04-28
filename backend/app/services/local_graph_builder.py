"""
本地图谱构建服务
不依赖 Zep API，从世界观结构化数据直接构建图谱
"""

import json
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from ..config import Config


class LocalGraphBuilder:
    """本地图谱构建器 — 零外部依赖"""

    def __init__(self):
        self.graphs_dir = os.path.join(Config.UPLOAD_FOLDER, "graphs")
        os.makedirs(self.graphs_dir, exist_ok=True)

    def create_graph(self, name: str) -> str:
        graph_id = f"local_{uuid.uuid4().hex[:16]}"
        graph = {
            "graph_id": graph_id,
            "name": name,
            "description": "WorldFish Worldview Graph",
            "ontology": {},
            "nodes": [],
            "edges": [],
            "created_at": time.time(),
        }
        self._save(graph_id, graph)
        return graph_id

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        graph = self._load(graph_id)
        graph["ontology"] = ontology
        self._save(graph_id, graph)

    def add_text_batches(
        self,
        graph_id: str,
        world_data: Dict[str, Any],
        progress_callback: Optional[Callable] = None,
    ) -> List[str]:
        """
        从世界观结构化数据构建节点和边（替代 Zep 的文本分块提取）

        Args:
            graph_id: 图谱ID
            world_data: 世界观数据，包含 entities, events, settings 等
            progress_callback: 进度回调 (message, progress_ratio)

        Returns:
            episode ID 列表（兼容接口，本地模式返回空列表）
        """
        graph = self._load(graph_id)
        nodes = list(graph["nodes"])
        edges = list(graph["edges"])
        node_id_set = {n["uuid"] for n in nodes}

        if progress_callback:
            progress_callback("从实体构建节点...", 0.1)

        # 1. 从 entities 创建节点
        for i, entity in enumerate(world_data.get("entities") or []):
            node_id = entity.get("id") or f"ent_{uuid.uuid4().hex[:12]}"
            if node_id in node_id_set:
                continue
            node_id_set.add(node_id)

            attr_text_parts = []
            for k, v in (entity.get("attributes") or {}).items():
                if v is not None:
                    attr_text_parts.append(f"{k}: {v}")

            nodes.append({
                "uuid": node_id,
                "name": entity.get("name", ""),
                "labels": [entity.get("type", "Entity")],
                "summary": "; ".join(attr_text_parts) if attr_text_parts else entity.get("name", ""),
                "attributes": entity.get("attributes") or {},
                "created_at": entity.get("created_at") or time.time(),
            })

        if progress_callback:
            progress_callback(f"已创建 {len(nodes)} 个实体节点", 0.3)

        # 2. 从 settings.items 创建节点
        settings = world_data.get("settings") or {}
        for i, item in enumerate(settings.get("items") or []):
            if not isinstance(item, dict):
                continue
            node_id = item.get("id") or f"set_{uuid.uuid4().hex[:12]}"
            if node_id in node_id_set:
                continue
            node_id_set.add(node_id)

            nodes.append({
                "uuid": node_id,
                "name": item.get("name", ""),
                "labels": [item.get("category", "Setting"), "Setting"],
                "summary": item.get("description") or item.get("detailContent") or "",
                "attributes": {
                    "category": item.get("category", ""),
                    "aliases": item.get("aliases") or [],
                },
                "created_at": time.time(),
            })

        if progress_callback:
            progress_callback(f"已创建 {len(nodes)} 个节点（含设定）", 0.5)

        # 3. 从 events 创建边（事件关联实体）
        entity_name_to_id = {n["name"]: n["uuid"] for n in nodes if n["name"]}
        edge_id_counter = 0

        for event in world_data.get("events") or []:
            event_entities = event.get("entities") or []
            if len(event_entities) < 2:
                continue

            # 为事件中的每对实体创建 RELATED_TO 边
            for i, name_a in enumerate(event_entities):
                id_a = entity_name_to_id.get(name_a)
                if not id_a:
                    continue
                for name_b in event_entities[i + 1:]:
                    id_b = entity_name_to_id.get(name_b)
                    if not id_b:
                        continue
                    edge_id = f"edge_{edge_id_counter}"
                    edge_id_counter += 1
                    edges.append({
                        "uuid": edge_id,
                        "name": "RELATED_TO",
                        "fact": f"共同参与事件「{event.get('name', '')}」: {event.get('description', '')[:200]}",
                        "fact_type": "RELATED_TO",
                        "source_node_uuid": id_a,
                        "target_node_uuid": id_b,
                        "attributes": {
                            "event": event.get("name", ""),
                            "date": event.get("date", ""),
                        },
                        "created_at": time.time(),
                        "valid_at": event.get("date", ""),
                    })

        if progress_callback:
            progress_callback(f"已创建 {len(edges)} 条关系边", 0.7)

        # 4. 从 mapData 创建地理关系边
        map_data = settings.get("mapData") or {}
        for field, relation_type in [
            ("regionRelations", "REGION_RELATION"),
            ("countryRelations", "COUNTRY_RELATION"),
            ("importantLocations", "LOCATED_AT"),
        ]:
            text = map_data.get(field, "")
            if not text:
                continue
            lines = text.split("\n") if isinstance(text, str) else text
            for line in lines:
                line = str(line).strip()
                if not line or len(line) < 5:
                    continue
                # 为每个地点关系创建 Location 节点 + 边
                loc_id = f"loc_{uuid.uuid4().hex[:8]}"
                nodes.append({
                    "uuid": loc_id,
                    "name": line[:100],
                    "labels": ["Location"],
                    "summary": line,
                    "attributes": {},
                    "created_at": time.time(),
                })
                # 尝试找到相关的实体节点
                for node in nodes:
                    node_name = node.get("name", "")
                    if node_name and len(node_name) > 1 and node_name in line:
                        edge_id = f"edge_{edge_id_counter}"
                        edge_id_counter += 1
                        edges.append({
                            "uuid": edge_id,
                            "name": relation_type,
                            "fact": line[:200],
                            "fact_type": relation_type,
                            "source_node_uuid": node["uuid"],
                            "target_node_uuid": loc_id,
                            "attributes": {},
                            "created_at": time.time(),
                        })
                        break

        if progress_callback:
            progress_callback(f"图谱构建完成: {len(nodes)} 节点, {len(edges)} 边", 0.9)

        # 5. 保存
        graph["nodes"] = nodes
        graph["edges"] = edges
        self._save(graph_id, graph)

        return []

    def _wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600,
    ):
        """本地模式：无需等待，直接完成"""
        if progress_callback:
            progress_callback("本地图谱构建完成", 1.0)

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        graph = self._load(graph_id)
        return {
            "graph_id": graph_id,
            "nodes": graph.get("nodes", []),
            "edges": graph.get("edges", []),
            "node_count": len(graph.get("nodes", [])),
            "edge_count": len(graph.get("edges", [])),
        }

    def delete_graph(self, graph_id: str):
        path = self._path(graph_id)
        if os.path.exists(path):
            os.remove(path)

    def _path(self, graph_id: str) -> str:
        return os.path.join(self.graphs_dir, f"{graph_id}.json")

    def _load(self, graph_id: str) -> Dict[str, Any]:
        path = self._path(graph_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "graph_id": graph_id,
            "nodes": [],
            "edges": [],
            "ontology": {},
        }

    def _save(self, graph_id: str, graph: Dict[str, Any]):
        with open(self._path(graph_id), "w", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)
