"""
世界观提取服务
严格按照项目中已建立的世界观体系进行内容提取
支持并行 AI 调用，细粒度提取，确保不遗漏内容
章节感知切分，全文处理无遗漏
"""

from app.utils.llm_client import LLMClient
from app.utils.logger import get_logger
import json
import re
import threading
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = get_logger('worldfish.service.enhanced_world_extractor')

# 每线程独立的 LLMClient，避免共享阻塞
_thread_local = threading.local()


class EnhancedWorldExtractor:
    """世界观提取器 — 章节感知 + 并行细粒度提取"""

    SETTING_CATEGORIES = {
        "character": ["character", "人物", "角色", "种族", "生物", "身份", "阵营成员"],
        "item": ["item", "物品", "道具", "装备", "资源", "科技", "技术", "建筑"],
        "organization": ["organization", "组织", "势力", "国家", "政权", "教会", "团体"],
        "geography": ["geography", "地理", "地点", "区域", "城市", "地貌", "环境"],
        "ability": ["ability", "能力", "魔法", "法术", "规则", "体系", "超凡"],
        "other": ["other", "其他", "文化", "历史", "社会", "经济", "设定"],
    }

    # 分段阈值
    LONG_TEXT_THRESHOLD = 15000
    CHUNK_SIZE = 8000
    CHUNK_OVERLAP = 300
    CHAPTERS_PER_CHUNK = 10  # 每批合并 10 章为一次 LLM 调用
    OUTER_WORKERS = 20       # 并行 LLM 调用数

    # 章节标记正则
    CHAPTER_RE = (
        r'(?:^|\n)\s*'
        r'(?:'
        r'第[零一二三四五六七八九十百千\d]+[部卷章节]'
        r'|Chapter\s+\d+'
        r'|Part\s+\d+'
        r'|VOLUME\s*\d+'
        r'|[第]?[零一二三四五六七八九十百千\d]+章[\s\n]'
        r')'
    )

    def __init__(self):
        self.errors: List[str] = []

    @staticmethod
    def _get_client():
        """每线程独立 LLMClient，8 线程x独立连接池 = 8 倍并发"""
        if not hasattr(_thread_local, 'client'):
            _thread_local.client = LLMClient()
        return _thread_local.client

    def _record_error(self, stage: str, error: Exception):
        message = str(error).strip()
        logger.error(f"{stage}失败: {message}")
        self.errors.append(f"{stage}: {message}")

    def _has_meaningful_content(self, data: Dict[str, Any]) -> bool:
        if not isinstance(data, dict):
            return False
        if data.get("entities") or data.get("events"):
            return True
        wi = data.get("world_info") or {}
        if isinstance(wi, dict) and any(str(v).strip() for v in wi.values() if v is not None):
            return True
        settings = data.get("settings") or {}
        if isinstance(settings, dict):
            for v in settings.values():
                if isinstance(v, (dict, list)) and v:
                    return True
                if isinstance(v, str) and v.strip():
                    return True
        return False

    # ==================== 规范化 ====================

    def _normalize_setting_category(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return "other"
        for category, keywords in self.SETTING_CATEGORIES.items():
            if normalized == category:
                return category
            if any(keyword.lower() in normalized for keyword in keywords):
                return category
        return "other"

    def _to_string_list(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(i).strip() for i in value if str(i).strip()]
        if isinstance(value, str):
            for sep in [",", "，", "、", "/", "|"]:
                if sep in value:
                    return [p.strip() for p in value.split(sep) if p.strip()]
            return [value.strip()] if value.strip() else []
        return []

    def _normalize_setting_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        name = str(item.get("name") or item.get("title") or item.get("label") or "").strip()
        desc = str(item.get("description") or item.get("summary") or item.get("content") or "").strip()
        detail = str(item.get("detailContent") or item.get("detail") or desc).strip()
        if not name or not (desc or detail):
            return {}
        return {
            "name": name, "settingType": "setting",
            "category": self._normalize_setting_category(item.get("category") or item.get("type")),
            "description": desc or detail, "aliases": self._to_string_list(item.get("aliases") or item.get("alias")),
            "detailContent": detail or desc,
        }

    def _normalize_calendar(self, item: Dict[str, Any]) -> Dict[str, Any]:
        name = str(item.get("name") or item.get("title") or "").strip()
        if not name:
            return {}
        ct = str(item.get("type") or item.get("calendarKind") or "纪元").strip()
        if "纪年" not in ct:
            ct = "纪元"
        bt = str(item.get("baseTime") or item.get("startYear") or item.get("start") or "").strip()
        et = str(item.get("endYear") or item.get("end") or "").strip()
        tr = str(item.get("timeRange") or "").strip()
        if not tr and bt:
            tr = f"{bt} ~ {et or '无'}"
        ratio = str(item.get("ratio") or "×1").strip() or "×1"
        if not ratio.startswith("×"):
            ratio = f"×{ratio}"
        return {
            "name": name, "type": ct, "baseTime": bt, "timeRange": tr,
            "unit": str(item.get("unit") or "年").strip() or "年", "ratio": ratio,
            "calendarType": str(item.get("calendarType") or item.get("monthDaySystem") or "未开启").strip() or "未开启",
            "description": str(item.get("description") or item.get("detail") or "").strip(),
        }

    def _normalize_map_text(self, value: Any) -> str:
        if isinstance(value, list):
            return "\n".join(str(i).strip() for i in value if str(i).strip())
        if isinstance(value, dict):
            return "\n".join(f"{k}: {i}" for k, i in value.items() if str(i).strip())
        return str(value or "").strip()

    def _normalize_settings(self, settings: Any) -> Dict[str, Any]:
        normalized = {
            "items": [], "mapData": {"regionRelations": "", "countryRelations": "", "importantLocations": ""}, "calendars": [],
        }
        if not isinstance(settings, dict):
            return normalized
        raw_items = settings.get("items") if isinstance(settings.get("items"), list) else []
        raw_cals = settings.get("calendars") if isinstance(settings.get("calendars"), list) else []
        raw_map = settings.get("mapData") if isinstance(settings.get("mapData"), dict) else {}
        normalized["items"] = [
            ni for item in raw_items if isinstance(item, dict) for ni in [self._normalize_setting_item(item)] if ni
        ]
        normalized["calendars"] = [
            nc for item in raw_cals if isinstance(item, dict) for nc in [self._normalize_calendar(item)] if nc
        ]
        normalized["mapData"] = {
            "regionRelations": self._normalize_map_text(raw_map.get("regionRelations") or raw_map.get("区域关系")),
            "countryRelations": self._normalize_map_text(raw_map.get("countryRelations") or raw_map.get("国家关系")),
            "importantLocations": self._normalize_map_text(raw_map.get("importantLocations") or raw_map.get("重要地点")),
        }
        if not raw_items and not raw_cals and not raw_map:
            for key, value in settings.items():
                if key in {"items", "mapData", "calendars"}:
                    continue
                text = self._normalize_map_text(value)
                if not text:
                    continue
                normalized["items"].append({
                    "name": str(key).strip(), "settingType": "setting",
                    "category": self._normalize_setting_category(key),
                    "description": text, "aliases": [], "detailContent": text,
                })
        return normalized

    # ==================== 综合提取 ====================

    _EXTRACTION_SYSTEM_PROMPT = """你是一个小说世界观提取引擎。从给定文本中提取所有世界观构建所需的数据。

核心原则：
1. 同一人物的不同名字/身份/称号必须识别并合并。例如"张小凡"幼年叫"小凡"，成为掌门后称"张真人"，这些是同一人，用 aliases 字段记录所有称呼。
2. 每个事件必须判断发生时间：是历史（主线开始前）、现在（主线进行中）还是未来（推测/后期）。结合上下文线索估算大概时间点。
3. 角色实体的生平叙述没有字数上限——必须穷尽该角色在文本中的所有经历。此外还必须显式追踪实力的变化和性格的变化，每次变化都要指出触发事件和时间节点。

提取以下四类信息：

=== 一、world_info 世界观基本信息 ===
- name: 作品名称
- description: 世界观概述（100-200字）
- era: 时代背景
- anchor_time: 故事主线时间点

=== 二、entities 实体列表 ===
类型：人物/国家/组织/种族/地点/物品/能力/其他

每个实体：name, type, aliases(所有别名/称号/化名列表), attributes
attributes 必须包含：

对**人物**类型：
- "简介": 完整生平叙述，**没有字数上限**。从首次出场到最终状态的所有经历、成长、关系、转折——有多少写多少
- "实力变化": 数组，记录角色的每一次实力/能力变化：
  [{{"时间节点":"第一卷中期/第X章/某事件后", "变化前":"序列7-学徒", "变化后":"序列6-正式成员", "触发事件":"通过考核/服用魔药/击败敌人", "描述":"变化的具体过程和表现"}}, ...]
- "性格变化": 数组，记录角色的每一次性格/心理变化：
  [{{"时间节点":"某事件后/某个阶段", "变化前":"天真善良", "变化后":"冷漠谨慎", "触发事件":"被背叛/亲人死亡/重大打击", "描述":"变化的具体表现"}}, ...]
- "关键转折": 数组，记录角色人生的重大转折点：
  [{{"时间节点":"...", "事件":"...", "影响":"..."}}, ...]
- 其他属性（身份、能力等级、所属组织等）

对**非人物**类型：
- "简介": 完整描述，**没有字数上限**
- 其他相关属性

所有实体如果从原文中能识别出成长阶段，提取"阶段"数组：
[{{"名称":"序列5-角色名","时期":"第一卷","描述":"该阶段的身份和能力","属性":{{...}}}}]

=== 三、events 事件列表（极其重要！必须穷举） ===
事件是世界观构建的核心数据，必须穷举文本中所有事件，不论大小。

每个事件：name, description(起因+经过+结果+影响, 50-400字), entities(参与方列表)
外加：
- time_type: "past"(历史/主线前)/"present"(主线中)/"future"(推测/主线后)/"unknown"(无法判断)
- estimated_date: 基于上下文线索估算的具体时间，无法估算时填"未知"
- date: 原文明确的事件时间，无明确时间则留空

**以下全部算事件，必须提取**：
- 战争、战役、战斗、冲突、政变、叛乱、革命
- 人物死亡、诞生、失踪、出现、复活、转世
- 组织/国家成立、解散、分裂、合并、改名
- 结盟、背叛、断交、建交、婚约、联姻
- 发现（新大陆/新技术/新资源）、发明、突破
- 灾难（天灾/人祸/瘟疫）、危机
- 晋升、退位、传位、禅让、夺权
- 重要对话/会议中揭示的关键信息、秘密
- 角色的每一次实力突破、性格转变的触发时刻
- 关键物品的获得/丢失/损毁
- **只要是文中明确提到"发生了"的事情，不论篇幅长短，都要提取**

**重要**：events 数组的长度应该与文中实际发生的事件数量相当。一篇长文中通常有数十甚至上百个事件。不要因为担心 JSON 太长而省略事件。

=== 四、settings 设定信息 ===
1. items: 世界观设定条目，类别 character|item|organization|geography|ability|other
   每项：name, category, description(50-150字), detailContent(100-500字), aliases
2. mapData: regionRelations, countryRelations, importantLocations
3. calendars: 历法纪元体系"""

    def _extract_from_chunk(self, chunk: str, chunk_index: int = 0) -> Dict[str, Any]:
        """单次 LLM 调用穷举提取一个文本块的所有世界观数据"""
        if chunk_index == 0:
            context = "这是整个文本的开头部分（含多章），世界观基础设定通常在此。"
        else:
            context = "这是文本的第%d个章节组（含多章）。请提取本段中出现的所有信息。" % (chunk_index + 1)

        prompt = """%s

扫描以下文本，穷举所有世界观数据。重点：
- 同一人物的不同称呼（本名、化名、称号、职称）要识别并用 aliases 字段记录
- 每个事件要判断 time_type（past/present/future）并估算 estimated_date
- 人物实体的"简介"没有字数上限，有多少经历写多少
- 必须提取人物的"实力变化"和"性格变化"，每个变化注明时间节点和触发事件

==== 文本开始 ====
%s
==== 文本结束 ====

返回 JSON 结构：
{
  "world_info": {
    "name": "世界观名称（仅第一个分段填写）",
    "description": "概述（100-200字）",
    "era": "时代背景",
    "anchor_time": "锚定时间"
  },
  "entities": [
    {
      "name": "实体名称（使用最常用或最终的名字作为主名）",
      "type": "人物/国家/组织/种族/地点/物品/能力/其他",
      "aliases": ["别名1", "称号2", "化名3"],
      "attributes": {
        "简介": "完整生平叙述，没有字数上限。穷尽本文中该角色/实体的所有经历",
        "实力变化": [
          {"时间节点": "第一卷中期/某事件后", "变化前": "序列7", "变化后": "序列6", "触发事件": "通过考核", "描述": "变化过程"}
        ],
        "性格变化": [
          {"时间节点": "某事件后", "变化前": "天真", "变化后": "冷漠", "触发事件": "被背叛", "描述": "变化表现"}
        ],
        "关键转折": [
          {"时间节点": "...", "事件": "...", "影响": "..."}
        ]
      }
    }
  ],
  "events": [
    {
      "name": "事件名称（20字以内）",
      "description": "起因+经过+结果+影响（50-400字，小事件可从简）",
      "time_type": "past/present/future/unknown",
      "estimated_date": "基于上下文线索估算的时间（如'开篇前约50年'、'第二卷中期'），无法估算填'未知'",
      "date": "原文明确的事件时间，无则留空",
      "entities": ["参与实体名"]
    }
  ],
  "settings": {
    "items": [
      {
        "name": "设定名称",
        "category": "character|item|organization|geography|ability|other",
        "description": "摘要（50-150字）",
        "aliases": ["别名"],
        "detailContent": "设定描述（100-500字）"
      }
    ],
    "mapData": {"regionRelations": "...", "countryRelations": "...", "importantLocations": "..."},
    "calendars": [
      {"name": "历法名称", "type": "纪元/纪年", "baseTime": "起始年份", "timeRange": "如 1200~1400", "unit": "年", "ratio": "x1", "calendarType": "未开启", "description": "历法说明"}
    ]
  }
}

要求：
1. entities 穷举所有有名实体，同人不同名必须用 aliases 合并。简介没有字数上限。人物必须填"实力变化"和"性格变化"数组，每次变化注明时间节点和触发事件
2. **events 极其重要**：穷举文本中发生的所有事件，不论大小。description 写清起因经过结果（50-400字，小事件可简略）。必须填 time_type 和 estimated_date。不要因为担心 JSON 太长而省略事件——一篇长文中的事件数量应该非常多
3. settings.items 的 detailContent 需详实（100-500字），category 严格用英文
4. world_info 仅第一个分段填写，后续分段返回空对象
5. 基于原文提取，不编造信息""" % (context, chunk)

        result = self._get_client().chat_json(
            messages=[
                {"role": "system", "content": self._EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=32768,
        )

        if not isinstance(result, dict):
            raise ValueError(f"章节组{chunk_index + 1}提取返回格式无效")

        entities = result.get("entities") or []
        if isinstance(entities, list):
            entities = [e for e in entities if isinstance(e, dict) and str(e.get("name", "")).strip()]
            for e in entities:
                e["name"] = str(e["name"]).strip()
                if "type" not in e or not e["type"]:
                    e["type"] = "其他"
                if "attributes" not in e or not isinstance(e.get("attributes"), dict):
                    e["attributes"] = {}
                if "aliases" not in e or not isinstance(e.get("aliases"), list):
                    e["aliases"] = []

        events = result.get("events") or []
        if isinstance(events, list):
            events = [e for e in events if isinstance(e, dict) and str(e.get("name", "")).strip()]
            for e in events:
                e["name"] = str(e["name"]).strip()
                e.setdefault("description", "")
                e.setdefault("date", "")
                e.setdefault("time_type", "unknown")
                e.setdefault("estimated_date", "未知")
                if "entities" not in e or not isinstance(e.get("entities"), list):
                    e["entities"] = []

        settings = self._normalize_settings(result.get("settings") or {})
        world_info = result.get("world_info") or {}

        chunk_result = {
            "world_info": world_info if chunk_index == 0 else {},
            "entities": entities,
            "events": events,
            "settings": settings,
        }

        # 验证提取结果
        validation_errors = self._validate_chunk_result(chunk_result, chunk_index)
        if validation_errors:
            critical = [e for e in validation_errors if "name 缺失" in e]
            if critical:
                logger.error(f"章节组{chunk_index + 1}有 {len(critical)} 个致命验证错误")
                self._record_error(f"章节组{chunk_index + 1}验证", ValueError("; ".join(critical[:5])))

        logger.info(f"章节组{chunk_index + 1}: {len(entities)} 实体, {len(events)} 事件, {len(settings.get('items', []))} 设定条目, {len(validation_errors)} 验证问题")
        return chunk_result

    # ==================== 整体提取流程 ====================

    def extract_from_text(self, text: str, progress_callback=None) -> Dict[str, Any]:
        """从文本提取世界观信息（章节感知全文处理，无遗漏）"""
        text_len = len(text) if text else 0
        if text_len <= self.LONG_TEXT_THRESHOLD:
            return self._extract_short_text(text, progress_callback)
        logger.info(f"文本 {text_len} 字符 -> 章节感知全量提取")
        return self._extract_chapters(text, progress_callback)

    def _extract_short_text(self, text: str, progress_callback=None) -> Dict[str, Any]:
        """短文本提取：主提取与文风分析并行，节省一次 LLM 往返"""
        self.errors = []
        try:
            # 主提取和文风分析互不依赖，并行执行
            from concurrent.futures import ThreadPoolExecutor, as_completed

            if progress_callback:
                progress_callback('extracting', 10, '正在提取世界观信息 + 分析文风...')

            extraction_result = [None]
            style_result = [None]
            errors = []

            def do_extract():
                try:
                    return self._extract_from_chunk(text, chunk_index=0)
                except Exception as e:
                    errors.append(e)
                    return None

            def do_style():
                try:
                    return self.extract_writing_style(text)
                except Exception as e:
                    self._record_error("文风分析", e)
                    return {"writing_style": "", "reference_text": text[:2000] if text else ""}

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_extract = executor.submit(do_extract)
                future_style = executor.submit(do_style)

                for future in as_completed([future_extract, future_style]):
                    if future == future_extract:
                        extraction_result[0] = future.result()
                        if progress_callback:
                            progress_callback('extracting', 50, '世界观提取完成，等待文风分析...')
                    else:
                        style_result[0] = future.result()

            if errors or extraction_result[0] is None:
                raise ValueError("世界观提取失败，请检查 LLM 配置。详细错误：" + " | ".join(str(e) for e in errors))

            result = extraction_result[0]
            style_data = style_result[0] or {"writing_style": "", "reference_text": text[:2000] if text else ""}

            if progress_callback:
                progress_callback('finalizing', 85, '正在整理结果...')

            extracted_data = {
                "world_info": result["world_info"],
                "entities": result["entities"],
                "events": result["events"],
                "settings": self._normalize_settings(result.get("settings") or {}),
                "writing_style": style_data.get("writing_style", ""),
                "reference_text": style_data.get("reference_text", ""),
            }

            if progress_callback:
                progress_callback('consolidating', 90, '正在整合修正所有提取结果...')
            extracted_data = self._consolidate_results(extracted_data)
            val_errors = self._validate_consolidated(extracted_data)
            if val_errors:
                logger.warning(f"整合后验证: {len(val_errors)} 个问题")

            if progress_callback:
                progress_callback('done', 95, f'提取完成: {len(extracted_data.get("entities", []))} 实体, {len(extracted_data.get("events", []))} 事件')

            logger.info(f"短文本提取完成: {len(extracted_data.get('entities', []))} 实体, {len(extracted_data.get('events', []))} 事件")
            return extracted_data
        except Exception as e:
            logger.error(f"提取世界观失败: {str(e)}")
            raise

    def _extract_chapters(self, text: str, progress_callback=None) -> Dict[str, Any]:
        """章节感知全量提取：文风分析+章节提取全部并行，不遗漏任何剧情"""
        self.errors = []
        chunks = self._chapter_aware_split(text)
        logger.info(f"章节切分: {len(text)} 字符 -> {len(chunks)} 个章节组（每 {self.CHAPTERS_PER_CHUNK} 章合并）")

        if len(chunks) == 1:
            return self._extract_short_text(chunks[0], progress_callback)

        max_workers = min(self.OUTER_WORKERS, len(chunks))
        all_results = [None] * len(chunks)
        style_result = [None]
        completed_count = [0]

        if progress_callback:
            progress_callback('extracting', 2,
                f'全量提取 {len(chunks)} 个章节组（{max_workers} 线程并行）+ 文风分析...')

        def process_chunk(idx, chunk):
            try:
                result = self._extract_from_chunk(chunk, chunk_index=idx)
                if self._has_meaningful_content(result):
                    return (idx, result)
            except Exception as e:
                self._record_error(f"章节组{idx + 1}", e)
            return (idx, None)

        def do_style():
            try:
                return self.extract_writing_style(chunks[0])
            except Exception as e:
                self._record_error("文风分析", e)
                return {"writing_style": "", "reference_text": chunks[0][:2000] if chunks[0] else ""}

        with ThreadPoolExecutor(max_workers=max_workers + 1) as executor:
            # 章节提取和文风分析全部并行
            future_style = executor.submit(do_style)
            futures = {executor.submit(process_chunk, i, c): i for i, c in enumerate(chunks)}

            for future in as_completed(futures):
                idx, result = future.result()
                if result:
                    all_results[idx] = result
                completed_count[0] += 1
                if progress_callback and len(chunks) > 0:
                    pct = 3 + int(completed_count[0] / len(chunks) * 85)
                    progress_callback('extracting', pct,
                        f'章节组 {completed_count[0]}/{len(chunks)} 完成...')

            # 等待文风分析完成
            style_result[0] = future_style.result()

        successful = [r for r in all_results if r is not None]
        if not successful:
            raise ValueError("所有章节提取均失败: " + " | ".join(self.errors))

        style_data = style_result[0] or {"writing_style": "", "reference_text": chunks[0][:2000] if chunks[0] else ""}

        if progress_callback:
            progress_callback('merging', 88, f'正在合并 {len(successful)} 个章节结果...')
        merged = self._merge_extractions(successful)
        merged["settings"] = self._normalize_settings(merged.get("settings") or {})
        merged["writing_style"] = style_data.get("writing_style", "")
        merged["reference_text"] = style_data.get("reference_text", "")

        if progress_callback:
            progress_callback('consolidating', 92, '正在整合修正所有提取结果（合并身份、修正时间线、丰富生平）...')
        merged = self._consolidate_results(merged)
        val_errors = self._validate_consolidated(merged)
        if val_errors:
            logger.warning(f"章节提取整合后验证: {len(val_errors)} 个问题")

        logger.info(f"章节提取完成: {len(merged.get('entities', []))} 实体, {len(merged.get('events', []))} 事件 ({len(successful)}/{len(chunks)})")
        return merged

    # ==================== 章节感知切分 ====================

    def _chapter_aware_split(self, text: str) -> List[str]:
        """按章节标记切分文本，每 CHAPTERS_PER_CHUNK 章合并为一次 LLM 调用"""
        chapter_starts = []
        for match in re.finditer(self.CHAPTER_RE, text, re.MULTILINE):
            pos = match.start()
            start = pos if text[pos] == '\n' else pos
            if text[start] == '\n':
                start = start + 1
            existing = {s for s, _ in chapter_starts}
            if start not in existing:
                chapter_starts.append((start, match.group().strip()))

        if not chapter_starts:
            logger.info("未检测到章节标记，使用等宽切分")
            return self._fallback_split(text)

        logger.info(f"检测到 {len(chapter_starts)} 个章节标记，每 {self.CHAPTERS_PER_CHUNK} 章合并为一段")

        # 每 CHAPTERS_PER_CHUNK 章合并为一个大段
        chunks = []
        for batch_start in range(0, len(chapter_starts), self.CHAPTERS_PER_CHUNK):
            batch_end = min(batch_start + self.CHAPTERS_PER_CHUNK, len(chapter_starts))
            start_pos = chapter_starts[batch_start][0]
            end_pos = chapter_starts[batch_end][0] if batch_end < len(chapter_starts) else len(text)
            chunk = text[start_pos:end_pos].strip()
            if len(chunk) >= 500:
                chunks.append(chunk)

        avg_len = sum(len(c) for c in chunks) // max(len(chunks), 1)
        logger.info(f"切分结果: {len(chunks)} 段 (平均 {avg_len} 字符)")
        return chunks

    def _fallback_split(self, text: str) -> List[str]:
        """等宽切分（无章节标记时的回退方案）"""
        if len(text) <= self.CHUNK_SIZE:
            return [text] if text.strip() else []
        chunks = []
        pos = 0
        while pos < len(text):
            end = min(pos + self.CHUNK_SIZE, len(text))
            if end < len(text):
                search_start = max(pos + self.CHUNK_SIZE // 2, pos)
                chunk_text = text[search_start:end + 500]
                best_break = -1
                for sep in ['\n\n\n', '\n\n', '\n第', '。\n', '！\n', '？\n', '。', '！', '？']:
                    idx = chunk_text.rfind(sep)
                    if idx >= 0:
                        candidate = search_start + idx + len(sep)
                        if candidate > pos + self.CHUNK_SIZE // 2:
                            best_break = candidate
                            break
                if best_break > 0:
                    end = best_break
            chunk = text[pos:end].strip()
            if chunk:
                chunks.append(chunk)
            pos = max(end - self.CHUNK_OVERLAP, pos + 1)
        return chunks

    # ==================== 合并 ====================

    def _merge_extractions(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并多个分段的提取结果（按名称去重，合并属性，保留最详细版本，O(n) 合并）"""
        merged_world_info = {}
        seen_entity_names = {}  # key -> entity object
        merged_entities = []
        seen_event_idx = {}     # key -> index in merged_events (O(1) lookup)
        merged_events = []
        seen_item_names = {}    # key -> item object
        merged_items = []
        seen_calendar_names = set()
        merged_calendars = []
        merged_map_region = []
        merged_map_country = []
        merged_map_locations = []

        for result in results:
            wi = result.get("world_info") or {}
            if not merged_world_info.get("name") and wi.get("name"):
                merged_world_info = wi

            for ent in result.get("entities") or []:
                name = str(ent.get("name", "")).strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen_entity_names:
                    existing = seen_entity_names[key]
                    new_attrs = ent.get("attributes") or {}
                    for ak, av in new_attrs.items():
                        if ak not in (existing.get("attributes") or {}):
                            existing.setdefault("attributes", {})[ak] = av
                    # 合并 aliases
                    new_aliases = ent.get("aliases") or []
                    existing_aliases = existing.setdefault("aliases", [])
                    for alias in new_aliases:
                        if alias not in existing_aliases:
                            existing_aliases.append(alias)
                else:
                    seen_entity_names[key] = ent
                    ent.setdefault("aliases", [])
                    merged_entities.append(ent)

            for evt in result.get("events") or []:
                name = str(evt.get("name", "")).strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen_event_idx:
                    existing = merged_events[seen_event_idx[key]]
                    if len(str(evt.get("description", ""))) > len(str(existing.get("description", ""))):
                        existing["description"] = evt["description"]
                else:
                    seen_event_idx[key] = len(merged_events)
                    merged_events.append(evt)

            settings = result.get("settings") or {}
            for item in settings.get("items") or []:
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen_item_names:
                    existing = seen_item_names[key]
                    nd = str(item.get("detailContent", ""))
                    od = str(existing.get("detailContent", ""))
                    if len(nd) > len(od):
                        existing["detailContent"] = nd
                else:
                    seen_item_names[key] = item
                    merged_items.append(item)

            for cal in settings.get("calendars") or []:
                name = str(cal.get("name", "")).strip().lower()
                if name and name not in seen_calendar_names:
                    seen_calendar_names.add(name)
                    merged_calendars.append(cal)

            map_data = settings.get("mapData") or {}
            for field, target in [
                ("regionRelations", merged_map_region),
                ("countryRelations", merged_map_country),
                ("importantLocations", merged_map_locations),
            ]:
                value = map_data.get(field)
                if isinstance(value, list):
                    for item in value:
                        s = str(item).strip()
                        if s and s not in target:
                            target.append(s)
                elif isinstance(value, str) and value.strip():
                    target.append(value.strip())

        return {
            "world_info": merged_world_info,
            "entities": merged_entities,
            "events": merged_events,
            "settings": {
                "items": merged_items,
                "mapData": {
                    "regionRelations": merged_map_region,
                    "countryRelations": merged_map_country,
                    "importantLocations": merged_map_locations,
                },
                "calendars": merged_calendars,
            },
        }

    # ==================== 验证 ====================

    VALID_ENTITY_TYPES = {"人物", "国家", "组织", "种族", "地点", "物品", "能力", "其他"}
    VALID_TIME_TYPES = {"past", "present", "future", "unknown"}

    def _validate_entity(self, entity: Dict[str, Any], idx: int) -> List[str]:
        """验证单个实体，返回错误列表"""
        errors = []
        name = str(entity.get("name", "")).strip()
        if not name:
            errors.append(f"实体#{idx}: name 缺失")
        etype = entity.get("type", "")
        if etype and etype not in self.VALID_ENTITY_TYPES:
            errors.append(f"实体'{name}': type='{etype}' 不在 {self.VALID_ENTITY_TYPES}")
        if not isinstance(entity.get("attributes"), dict):
            errors.append(f"实体'{name}': attributes 不是 dict")
        elif "简介" not in entity["attributes"]:
            errors.append(f"实体'{name}': attributes 缺少 '简介' 字段")
        if not isinstance(entity.get("aliases"), list):
            errors.append(f"实体'{name}': aliases 不是 list")
        return errors

    def _validate_event(self, event: Dict[str, Any], idx: int) -> List[str]:
        """验证单个事件，返回错误列表"""
        errors = []
        name = str(event.get("name", "")).strip()
        if not name:
            errors.append(f"事件#{idx}: name 缺失")
        if not event.get("description", "").strip():
            errors.append(f"事件'{name}': description 为空")
        tt = event.get("time_type", "")
        if tt and tt not in self.VALID_TIME_TYPES:
            errors.append(f"事件'{name}': time_type='{tt}' 不在 {self.VALID_TIME_TYPES}")
        if not isinstance(event.get("entities"), list):
            errors.append(f"事件'{name}': entities 不是 list")
        return errors

    def _validate_chunk_result(self, result: Dict[str, Any], chunk_index: int) -> List[str]:
        """验证单个章节组的提取结果，返回错误列表"""
        all_errors = []
        entities = result.get("entities") or []
        for i, e in enumerate(entities):
            all_errors.extend(self._validate_entity(e, i))
        events = result.get("events") or []
        for i, e in enumerate(events):
            all_errors.extend(self._validate_event(e, i))
        if all_errors:
            logger.warning(f"章节组{chunk_index + 1}验证发现 {len(all_errors)} 个问题: {all_errors[:10]}...")
        return all_errors

    def _validate_consolidated(self, data: Dict[str, Any]) -> List[str]:
        """验证整合后的最终结果"""
        all_errors = []
        for i, e in enumerate(data.get("entities") or []):
            all_errors.extend(self._validate_entity(e, i))
        for i, e in enumerate(data.get("events") or []):
            all_errors.extend(self._validate_event(e, i))
        return all_errors

    # ==================== 最终整合 ====================

    CONSOLIDATE_BATCH = 150  # 每批整合的实体数

    def _consolidate_results(self, merged: Dict[str, Any]) -> Dict[str, Any]:
        """最终 LLM 整合：合并同一人物的不同身份、修正时间线、丰富角色生平。超过批次上限时分批处理。"""
        entities = merged.get("entities", [])
        events = merged.get("events", [])
        if not entities and not events:
            return merged

        # 不超过一批的量直接整合
        if len(entities) <= self.CONSOLIDATE_BATCH and len(events) <= 120:
            return self._consolidate_batch(merged, entities, events)

        # 超出上限：分批整合实体，事件全部纳入第一批
        logger.info(f"数据量大 ({len(entities)} 实体, {len(events)} 事件)，分批整合...")
        all_consolidated_entities = []
        consolidated_events = events
        events_consolidated = False

        for batch_start in range(0, len(entities), self.CONSOLIDATE_BATCH):
            batch_end = min(batch_start + self.CONSOLIDATE_BATCH, len(entities))
            batch_entities = entities[batch_start:batch_end]
            batch_events = events if not events_consolidated else []
            batch_merged = dict(merged)
            batch_merged["entities"] = batch_entities
            batch_merged["events"] = batch_events

            result = self._consolidate_batch(batch_merged, batch_entities, batch_events)
            all_consolidated_entities.extend(result.get("entities", []))
            if not events_consolidated and result.get("events"):
                consolidated_events = result["events"]
                events_consolidated = True
            logger.info(f"整合批次 {batch_start//self.CONSOLIDATE_BATCH + 1}: {batch_end}/{len(entities)} 实体")

        merged["entities"] = all_consolidated_entities
        merged["events"] = consolidated_events
        return merged

    def _consolidate_batch(self, merged: Dict[str, Any], entities: List, events: List) -> Dict[str, Any]:
        """整合一批实体和事件（单次 LLM 调用）"""
        # 压缩字段以控制 prompt 长度，但不丢失关键信息
        entity_payload = []
        for e in entities:
            attrs = e.get("attributes") or {}
            item = {
                "name": e.get("name"), "type": e.get("type"),
                "aliases": e.get("aliases", []),
                "简介": attrs.get("简介", "")[:300],
            }
            changes = attrs.get("实力变化")
            if changes:
                item["实力变化"] = changes[:8]
            changes = attrs.get("性格变化")
            if changes:
                item["性格变化"] = changes[:8]
            changes = attrs.get("关键转折")
            if changes:
                item["关键转折"] = changes[:8]
            entity_payload.append(item)

        event_payload = []
        for e in (events or []):
            event_payload.append({
                "name": e.get("name"),
                "description": (e.get("description", ""))[:150],
                "time_type": e.get("time_type", "unknown"),
                "estimated_date": e.get("estimated_date", "未知"),
                "date": e.get("date", ""),
                "entities": e.get("entities", []),
            })

        summary = {
            "world_info": merged.get("world_info", {}),
            "entities_count": len(entities),
            "events_count": len(events),
            "entities": entity_payload,
            "events": event_payload,
        }

        prompt = """你是世界观数据整合专家。请对以下数据整合修正。

共 %d 实体, %d 事件。

工作：
1. **身份合并**：识别同一人物的不同称呼，合并数据。name 用最常用名，aliases 列全。合并"简介"、实力变化、性格变化、关键转折，按时间排序。
2. **时间线修正**：修正 time_type 和 estimated_date，确保时序正确。
3. **生平补充**：简介不设上限，穷尽经历。补充缺失的实力/性格变化，注明时间节点和触发事件。

==== 数据 ====
%s

返回 JSON：
{"entities":[{"name":"主名","type":"类型","aliases":["别名"],"attributes":{"简介":"完整生平","实力变化":[],"性格变化":[],"关键转折":[]}}],"events":[{"name":"事件","description":"描述","time_type":"past/present/future/unknown","estimated_date":"时间","date":"原文时间","entities":["参与方"]}],"consolidation_notes":"合并了哪些身份，修正了哪些时间"}""" % (len(entities), len(events), json.dumps(summary, ensure_ascii=False, indent=2))

        try:
            result = self._get_client().chat_json(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=16384,
            )
            if isinstance(result, dict):
                if result.get("entities"):
                    merged["entities"] = result["entities"]
                if result.get("events"):
                    merged["events"] = result["events"]
                logger.info(f"整合批次完成: {len(result.get('entities', []))} 实体, {len(result.get('events', []))} 事件")
        except Exception as e:
            self._record_error("整合批次", e)
            logger.warning(f"整合批次失败，保留原始数据: {e}")

        return merged

    # ==================== 文风 & 验证 ====================

    def extract_writing_style(self, text: str) -> Dict[str, str]:
        """从文本中分析提取文风特征"""
        try:
            sample = text[:3000]
            if len(text) > 3000:
                sample += "\n...(文本过长，此处为开头片段)"
            prompt = (
                "你是一位文学风格分析专家。请仔细阅读以下文本片段，分析其写作风格特征。\n\n"
                f"文本片段：\n{sample}\n\n"
                "请从以下维度分析文风：\n"
                "1. 叙事视角 2. 语言风格 3. 句式特点 4. 修辞手法 5. 节奏控制 6. 情感基调\n\n"
                '返回 JSON：{"writing_style":"一段简洁的文风描述（50-200字），用于指导AI模仿写作",'
                '"analysis":"详细的文风分析（100-300字）"}\n'
                "注意：只基于文本中实际展现的风格进行分析。"
            )
            result = self._get_client().chat_json(messages=[{"role": "user", "content": prompt}])
            if not isinstance(result, dict):
                raise ValueError("文风提取返回格式无效")
            return {
                "writing_style": str(result.get("writing_style", "") or "").strip(),
                "reference_text": text[:2000],
            }
        except Exception as e:
            self._record_error("提取文风特征", e)
            return {"writing_style": "", "reference_text": text[:2000] if text else ""}

    def validate_extraction(self, extracted_data: Dict[str, Any]) -> Dict[str, Any]:
        """验证提取数据质量"""
        try:
            summary = {
                "entities_count": len(extracted_data.get("entities", [])),
                "events_count": len(extracted_data.get("events", [])),
                "settings_items_count": len((extracted_data.get("settings") or {}).get("items", [])),
                "sample_entities": (extracted_data.get("entities") or [])[:30],
                "sample_events": (extracted_data.get("events") or [])[:20],
            }
            prompt = f"""请验证以下世界观提取数据的质量：
{json.dumps(summary, ensure_ascii=False, indent=2)}

检查：
1. 实体是否有明显遗漏的类型？
2. 事件是否过于简略？
3. 如有明显遗漏请列出应补充的内容。

返回 JSON：{{"quality":"good|needs_improvement","suggestions":"修正建议","supplement":{{"entities":[],"events":[]}}}}"""
            result = self._get_client().chat_json(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
            if isinstance(result, dict) and result.get("supplement"):
                s = result["supplement"]
                if s.get("entities"):
                    extracted_data.setdefault("entities", []).extend(s["entities"])
                if s.get("events"):
                    extracted_data.setdefault("events", []).extend(s["events"])
            return extracted_data
        except Exception as e:
            self._record_error("验证提取数据", e)
            return extracted_data
