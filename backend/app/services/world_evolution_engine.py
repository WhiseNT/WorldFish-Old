"""LLM 驱动的世界观进化推演引擎"""

import threading
from typing import Any, Dict, List

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..models.evolution import Evolution, EvolutionRound, EvolutionManager
from ..models.world import WorldSetting

logger = get_logger("worldfish.evolution")


def _world_to_context(world: WorldSetting) -> str:
    """将世界观数据转换为 LLM 上下文文本"""
    parts = []

    parts.append(f"世界观名称: {world.name or '未命名'}")
    parts.append(f"时代背景: {world.era or '未知'}")
    parts.append(f"锚定时间: {world.anchor_time or '未知'}")
    parts.append(f"描述: {world.description or '无'}")

    if world.entities:
        parts.append(f"\n## 核心实体 ({len(world.entities)} 个)")
        for ent in world.entities:
            name = ent.name or "?"
            etype = ent.type or "?"
            attrs = ent.attributes or {}
            attr_str = "；".join(f"{k}: {v}" for k, v in attrs.items() if v)
            parts.append(f"- [{etype}] {name}" + (f" ({attr_str})" if attr_str else ""))

    if world.events:
        parts.append(f"\n## 关键事件 ({len(world.events)} 个)")
        for evt in world.events:
            date = evt.date or "?"
            name = evt.name or "?"
            desc = evt.description or ""
            parts.append(f"- {date}: {name} — {desc[:150]}")

    settings = world.settings if isinstance(world.settings, dict) else {}
    items = settings.get("items") or []
    if items:
        parts.append(f"\n## 世界观设定 ({len(items)} 个)")
        for item in items:
            if isinstance(item, dict):
                name = item.get("name", "?")
                cat = item.get("category", "?")
                desc = item.get("description") or item.get("detailContent") or ""
                parts.append(f"- [{cat}] {name}: {desc[:120]}")

    calendars = settings.get("calendars") or []
    if calendars:
        parts.append(f"\n## 历法体系 ({len(calendars)} 个)")
        for cal in calendars:
            if isinstance(cal, dict):
                parts.append(f"- {cal.get('name', '?')}: {cal.get('timeRange', '?')} ({cal.get('type', '?')})")

    map_data = settings.get("mapData") or {}
    if isinstance(map_data, dict):
        for key, label in [("regionRelations", "区域关系"), ("countryRelations", "国家关系"), ("importantLocations", "重要地点")]:
            text = map_data.get(key, "")
            if text:
                parts.append(f"\n## {label}\n{text[:500]}")

    return "\n".join(parts)


class WorldEvolutionEngine:
    """世界观进化推演引擎 — 逐轮调用 LLM 生成世界观进化叙事"""

    def __init__(self):
        self.llm_client = LLMClient()

    def evolve_async(self, evolution_id: str, world: WorldSetting, scenario: str, config: Dict[str, Any],
                     accumulated_context: Dict[str, Any] = None):
        """在后台线程中运行进化推演"""
        thread = threading.Thread(
            target=self._evolve_worker,
            args=(evolution_id, world, scenario, config, accumulated_context),
            daemon=True,
        )
        thread.start()

    def _evolve_worker(self, evolution_id: str, world: WorldSetting, scenario: str, config: Dict[str, Any],
                       accumulated_context: Dict[str, Any] = None):
        try:
            rounds = int(config.get("rounds", 5))
            temperature = float(config.get("temperature", 0.7))
            time_span_start = config.get("time_span_start") or world.anchor_time or "未知"
            focus_areas = config.get("focus_areas") or []

            world_context = _world_to_context(world)

            accumulated_narrative = []

            # 如果是分支推演，加载父进化的叙事作为前缀上下文
            if accumulated_context and accumulated_context.get('parent_narratives'):
                accumulated_narrative = list(accumulated_context['parent_narratives'])

            # 提取世界中存在的实体名称集合，用于事实校验
            known_entity_names = {e.name for e in (world.entities or []) if e.name}

            for r in range(1, rounds + 1):
                logger.info(f"进化轮次 {r}/{rounds} (evolution_id={evolution_id})")
                round_result = self._run_round(
                    world_context=world_context,
                    scenario=scenario,
                    round_number=r,
                    total_rounds=rounds,
                    time_span_start=time_span_start,
                    focus_areas=focus_areas,
                    previous_rounds=accumulated_narrative,
                    temperature=temperature,
                )
                # 事实一致性校验：过滤 LLM 幻觉产生的实体
                round_result = self._validate_round_result(round_result, known_entity_names, r)

                evolution_round = EvolutionRound(
                    round_number=r,
                    narrative=round_result.get("narrative", ""),
                    year_advanced_to=round_result.get("year_advanced_to", ""),
                    affected_entities=round_result.get("affected_entities") or [],
                    new_events=round_result.get("new_events") or [],
                )
                EvolutionManager.add_round(evolution_id, evolution_round)
                accumulated_narrative.append(round_result.get("narrative", ""))

            EvolutionManager.update_status(evolution_id, "completed")
            logger.info(f"进化完成: {evolution_id}, {rounds} 轮")

        except Exception as e:
            logger.error(f"进化失败: {evolution_id}: {e}")
            try:
                EvolutionManager.update_status(evolution_id, "failed")
            except Exception:
                pass

    def _run_round(
        self,
        world_context: str,
        scenario: str,
        round_number: int,
        total_rounds: int,
        time_span_start: str,
        focus_areas: List[str],
        previous_rounds: List[str],
        temperature: float,
    ) -> Dict[str, Any]:
        focus_text = ""
        if focus_areas:
            focus_text = f"重点关注的领域: {', '.join(focus_areas)}。请在推演中着重展开这些方面的变化。"

        previous_text = ""
        if previous_rounds:
            recent = previous_rounds[-2:] if len(previous_rounds) > 2 else previous_rounds
            previous_text = "## 之前的推演进程\n" + "\n---\n".join(
                f"第{i+1}轮: {n[:300]}" for i, n in enumerate(recent)
            )

        prompt = f"""你是一个客观的世界观推演引擎。你的任务是基于给定的世界观设定，按照用户提出的推演需求，以客观第三方视角陈述世界演化的进程。

## 当前世界观状态
{world_context}

## 用户的推演需求/场景
{scenario}

{focus_text}

{previous_text}

## 当前推演进度
这是第 {round_number} 轮，共 {total_rounds} 轮。起始时间为 {time_span_start}。

请基于当前世界观状态和之前轮次的推演进程，以客观的第三方视角陈述这一轮的世界演化。

请严格以 JSON 格式返回：
{{
    "narrative": "本轮的叙事文本（200-500字），以客观第三方视角陈述这一时间段内发生的事件和变化。注意：不要模仿任何文学风格或文风，保持平实、客观的陈述语气，类似历史记载或百科全书。",
    "year_advanced_to": "本轮结束时的时间点（如：1352年秋、第三纪元1542年等）",
    "affected_entities": [
        {{
            "name": "受影响的实体名称",
            "state_changes": "该实体发生的变化描述（含实力、性格、地位等具体变化）",
            "new_status": "变化后的状态"
        }}
    ],
    "new_events": [
        {{
            "name": "新事件名称",
            "date": "事件发生时间",
            "description": "事件描述",
            "involved_entities": ["涉及的实体名1", "涉及的实体名2"]
        }}
    ]
}}

注意事项：
- narrative 必须使用客观第三方视角，平实陈述，不要模仿任何文学风格
- 叙事要有逻辑连贯性，承接前文
- 变化要基于世界观设定的内在逻辑
- 不要引入世界观中未提及的新元素，除非是合理推演
- affected_entities 只列出本轮中状态发生了实质变化的实体，必须描述具体的实力、性格、地位变化
- 如果是最后一轮，叙事应有一个合理的阶段性收尾"""

        # 带重试的 LLM 调用
        last_error = None
        for attempt in range(3):
            try:
                result = self.llm_client.chat_json(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature - (attempt * 0.1),
                    max_tokens=8192,
                )
                if not isinstance(result, dict):
                    raise ValueError(f"LLM 返回格式无效: {type(result)}")
                return result
            except Exception as e:
                last_error = e
                logger.warning(f"轮次{round_number} 第{attempt+1}次尝试失败: {str(e)[:100]}")
                if attempt < 2:
                    import time
                    time.sleep(2 * (attempt + 1))

        raise ValueError(f"轮次{round_number} LLM调用3次均失败: {last_error}")

    def _validate_round_result(self, result: Dict[str, Any], known_entities: set, round_num: int) -> Dict[str, Any]:
        """事实一致性校验：检测并过滤 LLM 幻觉产生的实体，修复缺失字段"""
        # 1. 校验 year_advanced_to
        if not result.get("year_advanced_to", "").strip():
            logger.warning(f"轮次{round_num}: year_advanced_to 为空，设为'未知'")
            result["year_advanced_to"] = "未知"

        # 2. 校验 affected_entities
        affected = result.get("affected_entities") or []
        hallucinated = []
        validated_affected = []
        for ae in affected:
            if not isinstance(ae, dict):
                continue
            name = str(ae.get("name", "")).strip()
            if not name:
                continue
            if name not in known_entities:
                hallucinated.append(name)
                logger.warning(f"轮次{round_num}: LLM 幻觉 — 实体'{name}'不在世界观中")
                continue
            ae.setdefault("state_changes", "")
            ae.setdefault("new_status", "")
            validated_affected.append(ae)
        if hallucinated:
            logger.warning(f"轮次{round_num}: 过滤掉 {len(hallucinated)} 个幻觉实体: {hallucinated}")
            result["affected_entities"] = validated_affected
            # 在叙事中追加警告
            result["narrative"] = (result.get("narrative", "") +
                f"\n\n[校验警告: 本轮LLM生成了 {len(hallucinated)} 个不存在于世界观中的实体，已自动过滤: {', '.join(hallucinated[:5])}]")

        # 3. 校验 new_events
        events = result.get("new_events") or []
        validated_events = []
        for evt in events:
            if not isinstance(evt, dict):
                continue
            name = str(evt.get("name", "")).strip()
            if not name:
                continue
            evt.setdefault("date", "")
            evt.setdefault("description", "")
            if not isinstance(evt.get("involved_entities"), list):
                evt["involved_entities"] = []
            # 标记不在世界观中的涉事实体，但不删除事件（事件可以引入新实体）
            involved = evt.get("involved_entities", [])
            unknown = [n for n in involved if n not in known_entities]
            if unknown:
                logger.info(f"轮次{round_num}: 事件'{name}'涉及未知实体 {unknown}（可能是推演产生的新实体）")
            validated_events.append(evt)
        result["new_events"] = validated_events

        # 4. 校验 narrative 不为空
        if not result.get("narrative", "").strip():
            logger.warning(f"轮次{round_num}: narrative 为空")
            result["narrative"] = f"第{round_num}轮推演未产生有效叙事。"

        return result
