"""世界观进化推演 API"""

from typing import Any, Dict, List

from flask import Blueprint, request, jsonify

from ..config import Config
from ..models.world import WorldManager
from ..models.evolution import Evolution, EvolutionManager
from ..services.world_evolution_engine import WorldEvolutionEngine
from ..utils.logger import get_logger

evolution_bp = Blueprint('evolution', __name__)
logger = get_logger('worldfish.api.evolution')


ENTITY_TYPE_TO_SETTING_CATEGORY = {
    '人物': 'character', '角色': 'character', 'person': 'character', 'character': 'character',
    '种族': 'character', '生物': 'character',
    '国家': 'organization', '政权': 'organization', '组织': 'organization', '势力': 'organization',
    'nation': 'organization', 'organization': 'organization', 'faction': 'organization',
    '团体': 'organization', '教会': 'organization', '公司': 'organization', '公会': 'organization',
    '地点': 'geography', '位置': 'geography', '城市': 'geography', 'location': 'geography',
    '地理': 'geography', '区域': 'geography',
    '物品': 'item', '道具': 'item', '装备': 'item', '武器': 'item', 'item': 'item',
    '能力': 'ability', '魔法': 'ability', '技能': 'ability', '体系': 'ability', 'ability': 'ability',
}


def _resolve_setting_category(entity_type: str) -> str:
    normalized_type = str(entity_type or '').strip()
    return ENTITY_TYPE_TO_SETTING_CATEGORY.get(normalized_type, 'other')


def _ensure_settings_items(world) -> List[Dict[str, Any]]:
    if not isinstance(world.settings, dict):
        world.settings = {'items': [], 'mapData': {}, 'calendars': []}
    items = world.settings.setdefault('items', [])
    if not isinstance(items, list):
        world.settings['items'] = []
        items = world.settings['items']
    return items


def _build_stage_payload(evolution_id: str, round_number: int, year_advanced_to: str, entity_change: Dict[str, Any]) -> Dict[str, Any]:
    new_status = str(entity_change.get('new_status', '') or '').strip()
    state_changes = str(entity_change.get('state_changes', '') or '').strip()
    stage_name = str(entity_change.get('stage_name', '') or '').strip()

    if not stage_name:
        if year_advanced_to and new_status:
            stage_name = f'{year_advanced_to} · {new_status}'
        elif year_advanced_to:
            stage_name = year_advanced_to
        elif new_status:
            stage_name = new_status
        else:
            stage_name = f'推演第{round_number}轮'

    return {
        'id': f'stage_{evolution_id}_{round_number}_{abs(hash(entity_change.get("name", ""))) % 100000}',
        'name': stage_name,
        'era': year_advanced_to,
        'description': state_changes or new_status,
        'attributes': {
            '状态': new_status,
            '状态变化': state_changes,
        },
        'source': {
            'type': 'evolution',
            'evolution_id': evolution_id,
            'round': round_number,
        },
    }


def _upsert_stage(entity, stage_payload: Dict[str, Any]) -> None:
    if not isinstance(entity.stages, list):
        entity.stages = []

    source = stage_payload.get('source') or {}
    for index, existing in enumerate(entity.stages):
        existing_source = existing.get('source') if isinstance(existing, dict) else None
        if not isinstance(existing_source, dict):
            continue
        if existing_source.get('evolution_id') == source.get('evolution_id') and existing_source.get('round') == source.get('round'):
            merged_attributes = dict(existing.get('attributes') or {})
            merged_attributes.update(stage_payload.get('attributes') or {})
            entity.stages[index] = {
                **existing,
                **stage_payload,
                'attributes': merged_attributes,
            }
            return

    entity.stages.append(stage_payload)


def _upsert_entity_setting(world, entity, stage_payload: Dict[str, Any]) -> bool:
    items = _ensure_settings_items(world)
    entity_id = getattr(entity, 'id', '')
    setting_item = None

    for item in items:
        if not isinstance(item, dict):
            continue
        if entity.setting_item_id and item.get('id') == entity.setting_item_id:
            setting_item = item
            break
        if item.get('linkedEntityId') == entity_id:
            setting_item = item
            break
        if item.get('name') == entity.name:
            setting_item = item
            break

    stage_label = stage_payload.get('name') or stage_payload.get('era') or '最新阶段'
    stage_summary = stage_payload.get('description') or stage_payload.get('attributes', {}).get('状态') or ''
    detail_line = f'[{stage_label}] {stage_summary}'.strip()

    if setting_item is None:
        setting_item = {
            'id': f'setting_{entity_id or entity.name}',
            'name': entity.name,
            'settingType': 'setting',
            'category': _resolve_setting_category(getattr(entity, 'type', '')),
            'description': stage_summary or getattr(entity, 'type', '') or '实体设定',
            'detailContent': detail_line,
            'aliases': [],
            'linkedEntityId': entity_id,
        }
        items.append(setting_item)
        entity.setting_item_id = setting_item['id']
        return True

    existing_detail = str(setting_item.get('detailContent', '') or '').strip()
    if detail_line and detail_line not in existing_detail:
        setting_item['detailContent'] = f'{existing_detail}\n{detail_line}'.strip() if existing_detail else detail_line

    setting_item['description'] = stage_summary or setting_item.get('description') or getattr(entity, 'type', '') or '实体设定'
    setting_item['linkedEntityId'] = entity_id
    if not entity.setting_item_id:
        entity.setting_item_id = str(setting_item.get('id') or '')
    return False


@evolution_bp.route('/create', methods=['POST'])
def create_evolution():
    """创建进化推演。支持向后推演 (forward) 和重新推演 (branch)"""
    try:
        data = request.get_json(silent=True) or {}
        world_id = data.get('world_id', '').strip()
        scenario = data.get('scenario', '').strip()
        config = data.get('config') or {}
        evolution_type = data.get('evolution_type', 'forward')  # "forward" | "branch"
        parent_evolution_id = data.get('parent_evolution_id', '')
        parent_round = data.get('parent_round', -1)

        if not world_id:
            return jsonify({'success': False, 'message': '请提供世界观 ID'}), 400
        if not scenario:
            return jsonify({'success': False, 'message': '请提供推演场景'}), 400
        if not Config.LLM_API_KEY:
            return jsonify({'success': False, 'message': 'LLM API Key 未配置'}), 400

        world = WorldManager.get_world(world_id)
        if not world:
            return jsonify({'success': False, 'message': '世界观不存在'}), 404

        # 如果是分支推演，积累父进化状态
        accumulated_context = None
        if parent_evolution_id and parent_round >= 0:
            parent_evo = EvolutionManager.get(parent_evolution_id)
            if parent_evo:
                accumulated_context = {
                    'parent_evolution_id': parent_evolution_id,
                    'parent_round': parent_round,
                    'parent_narratives': [
                        r.narrative for r in parent_evo.rounds[:parent_round + 1]
                    ],
                    'parent_affected_entities': [],
                }
                for r in parent_evo.rounds[:parent_round + 1]:
                    for ent in (r.affected_entities or []):
                        accumulated_context['parent_affected_entities'].append(ent)

        evolution = Evolution.create(
            world_id=world_id, scenario=scenario, config=config,
            parent_evolution_id=parent_evolution_id, parent_round=parent_round,
            evolution_type=evolution_type,
        )
        EvolutionManager.save(evolution)

        engine = WorldEvolutionEngine()
        engine.evolve_async(
            evolution.id, world, scenario, config,
            accumulated_context=accumulated_context,
        )

        logger.info(f"进化推演已启动: {evolution.id} (type={evolution_type}, world={world_id})")
        return jsonify({'success': True, 'evolution_id': evolution.id})

    except Exception as e:
        logger.error(f"创建进化推演失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@evolution_bp.route('/world/<world_id>', methods=['GET'])
def list_world_evolutions(world_id: str):
    """获取某世界观的所有进化推演（用于推演树）"""
    try:
        evolutions = EvolutionManager.list_by_world(world_id)
        return jsonify({
            'success': True,
            'evolutions': [e.to_dict() for e in evolutions],
        })
    except Exception as e:
        logger.error(f"获取推演列表失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@evolution_bp.route('/<evolution_id>/apply', methods=['POST'])
def apply_evolution_changes(evolution_id: str):
    """将推演中选定的变更应用到世界观"""
    try:
        evolution = EvolutionManager.get(evolution_id)
        if not evolution:
            return jsonify({'success': False, 'message': '推演不存在'}), 404

        data = request.get_json(silent=True) or {}
        selected_entities = data.get('entities', [])  # [{name, state_changes, new_status, round}]
        selected_events = data.get('events', [])       # [{name, date, description, involved_entities}]
        selected_round_numbers = data.get('rounds', [])  # 来自哪些轮次

        world = WorldManager.get_world(evolution.world_id)
        if not world:
            return jsonify({'success': False, 'message': '世界观不存在'}), 404

        applied = {'entities': 0, 'events': 0, 'settings_items': 0}

        round_year_map = {
            r.round_number: r.year_advanced_to
            for r in evolution.rounds
        }

        # 合并实体状态变化到世界观实体，并沉淀为阶段
        for ent in selected_entities:
            name = ent.get('name', '')
            round_number = int(ent.get('round') or (selected_round_numbers[-1] if selected_round_numbers else 0) or 0)
            year_advanced_to = round_year_map.get(round_number, '')
            stage_payload = _build_stage_payload(evolution_id, round_number, year_advanced_to, ent)
            # 查找已有实体或创建新实体
            target_entity = None
            for existing in world.entities:
                if existing.name == name:
                    target_entity = existing
                    break

            if not target_entity and name:
                from ..models.world import Entity
                target_entity = Entity.create(
                    world_id=world.id, name=name, type='推演实体',
                    attributes={
                        '当前状态': ent.get('new_status', ''),
                        '最近变化': ent.get('state_changes', ''),
                        '演化来源': f"evol:{evolution_id}",
                    },
                    stages=[stage_payload],
                    evolution_refs=[f'evol:{evolution_id}'],
                )
                world.entities.append(target_entity)

            if not target_entity:
                continue

            if not isinstance(target_entity.attributes, dict):
                target_entity.attributes = {}
            target_entity.attributes['当前状态'] = ent.get('new_status', '')
            target_entity.attributes['最近变化'] = ent.get('state_changes', '')
            target_entity.attributes['演化来源'] = f"evol:{evolution_id}"

            if not isinstance(target_entity.evolution_refs, list):
                target_entity.evolution_refs = []
            evolution_ref = f'evol:{evolution_id}'
            if evolution_ref not in target_entity.evolution_refs:
                target_entity.evolution_refs.append(evolution_ref)

            _upsert_stage(target_entity, stage_payload)
            created_setting = _upsert_entity_setting(world, target_entity, stage_payload)
            if created_setting:
                applied['settings_items'] += 1

            applied['entities'] += 1

        # 添加选定事件到世界观
        for evt in selected_events:
            name = evt.get('name', '')
            if name:
                from ..models.world import Event
                new_event = Event.create(
                    world_id=world.id,
                    name=name,
                    description=evt.get('description', ''),
                    date=evt.get('date', ''),
                    entities=evt.get('involved_entities') or [],
                )
                # 添加演化来源属性
                if not new_event.__dict__.get('evolution_ref'):
                    new_event.__dict__['evolution_ref'] = f"evol:{evolution_id}"
                world.events.append(new_event)
                applied['events'] += 1

        WorldManager.save_world(world)

        return jsonify({
            'success': True,
            'message': f"已应用 {applied['entities']} 实体变更, {applied['events']} 事件, {applied['settings_items']} 设定项",
            'applied': applied,
        })

    except Exception as e:
        logger.error(f"应用推演变更失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@evolution_bp.route('/<evolution_id>', methods=['GET'])
def get_evolution(evolution_id: str):
    """获取进化推演完整数据"""
    try:
        evolution = EvolutionManager.get(evolution_id)
        if not evolution:
            return jsonify({'success': False, 'message': '推演不存在'}), 404

        return jsonify({'success': True, 'evolution': evolution.to_dict()})

    except Exception as e:
        logger.error(f"获取进化推演失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@evolution_bp.route('/entity-chat', methods=['POST'])
def entity_chat():
    """与世界观中的实体进行角色扮演对话（支持阶段选择）"""
    try:
        data = request.get_json(silent=True) or {}
        world_id = data.get('world_id', '')
        entity_name = data.get('entity_name', '')
        question = data.get('question', '')
        stage_name = data.get('stage_name', '')  # 可选：实体的特定阶段

        if not all([world_id, entity_name, question]):
            return jsonify({'success': False, 'message': '缺少参数'}), 400
        if not Config.LLM_API_KEY:
            return jsonify({'success': False, 'message': 'LLM API Key 未配置'}), 400

        world = WorldManager.get_world(world_id)
        if not world:
            return jsonify({'success': False, 'message': '世界观不存在'}), 404

        # 查找实体及阶段信息
        entity = None
        for ent in world.entities:
            if ent.name == entity_name:
                entity = ent
                break

        if not entity:
            return jsonify({'success': False, 'message': f'实体 "{entity_name}" 不存在'}), 404

        attrs = dict(entity.attributes or {})
        stages = entity.stages or attrs.get("阶段") or attrs.get("stages") or []

        # 处理阶段选择
        stage_description = ""
        if stage_name:
            if isinstance(stages, list):
                for stage in stages:
                    if isinstance(stage, dict) and stage.get("名称", stage.get("name", "")) == stage_name:
                        stage_attrs = stage.get("属性", stage.get("attributes", {}))
                        stage_desc = stage.get("描述", stage.get("description", ""))
                        stage_era = stage.get("时期", stage.get("era", ""))
                        stage_description = f"\n当前阶段: {stage_name}"
                        if stage_era:
                            stage_description += f"\n阶段时期: {stage_era}"
                        if stage_desc:
                            stage_description += f"\n阶段描述: {stage_desc}"
                        # 合并阶段属性
                        if isinstance(stage_attrs, dict):
                            for k, v in stage_attrs.items():
                                attrs[k] = v
                        break

        # 构建实体详细信息
        attr_lines = []
        for k, v in attrs.items():
            if k in ("阶段", "stages"):
                continue  # 阶段信息单独处理
            if v:
                attr_lines.append(f"{k}: {v}")
        entity_profile = f"名称: {entity.name}\n类型: {entity.type or '未知'}\n"
        if attr_lines:
            entity_profile += "属性:\n" + "\n".join(f"  {line}" for line in attr_lines)
        if stage_description:
            entity_profile += stage_description

        # 列出实体可用的阶段（供前端选择）
        available_stages = []
        if isinstance(stages, list):
            for s in stages:
                if isinstance(s, dict):
                    s_name = s.get("名称", s.get("name", ""))
                    if s_name:
                        available_stages.append({
                            "name": s_name,
                            "era": s.get("时期", s.get("era", "")),
                            "description": (s.get("描述", s.get("description", "")) or "")[:200],
                        })

        # 相关事件
        related_events = []
        for evt in world.events:
            if entity_name in (evt.entities or []):
                related_events.append(f"- {evt.date or '?'}: {evt.name} — {evt.description or ''}")

        # 构建丰富的世界观上下文
        world_context = f"世界观名称: {world.name or '未命名'}\n"
        world_context += f"时代背景: {world.era or '未知'}\n"
        world_context += f"世界观描述: {world.description or '无'}\n"

        if world.writing_style:
            world_context += f"\n文风参考: {world.writing_style}"

        if related_events:
            world_context += f"\n\n与该角色相关的剧情事件:\n" + "\n".join(related_events[:8])

        # 增强的角色扮演 prompt
        system_prompt = f"""你正在角色扮演一个虚构世界中的角色。你必须完全沉浸在这个角色中。

{world_context}

【你要扮演的角色】
{entity_profile}

【角色扮演规则——必须严格遵守】
1. 你**就是**{entity_name}本人。用第一人称"我"回答。永远不要用"作为一个AI"、"根据设定"等出戏的表达。
2. 回答的口吻、用词、语气必须完全符合角色的身份、性格、能力等级和世界观。
3. 你拥有该角色在剧情中应有的全部记忆和知识，包括与该角色相关的所有事件。
4. 如果你被问到角色不知道的事，用符合角色性格的方式回应（如："我不清楚"、"那不是我能接触的"），但不要破坏沉浸感。
5. 回答长度根据问题灵活调整。简单问候简短回应（20-50字），涉及世界观或剧情的问题可以详细回答（100-300字）。
6. 说话风格要与世界观文风一致。如果世界是史诗奇幻，回答要有史诗感；如果是现代都市，回答要口语化自然。
7. 不要跳出角色进行解释或说明，你就是角色本人。"""

        user_prompt = f"问题: {question}\n\n请以{entity_name}的身份和口吻回答。"

        from ..utils.llm_client import LLMClient
        llm = LLMClient()
        reply = llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,
            max_tokens=1024,
        )

        return jsonify({
            'success': True,
            'reply': reply,
            'entity_name': entity_name,
            'available_stages': available_stages,
        })

    except Exception as e:
        logger.error(f"实体对话失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@evolution_bp.route('/<evolution_id>/status', methods=['GET'])
def get_evolution_status(evolution_id: str):
    """获取进化推演状态（轻量轮询端点）"""
    try:
        evolution = EvolutionManager.get(evolution_id)
        if not evolution:
            return jsonify({'success': False, 'message': '推演不存在'}), 404

        last_narrative = ''
        if evolution.rounds:
            last_narrative = evolution.rounds[-1].narrative[:200]

        return jsonify({
            'success': True,
            'status': evolution.status,
            'current_round': len(evolution.rounds),
            'total_rounds': evolution.config.get('rounds', 5) if evolution.config else 5,
            'last_narrative': last_narrative,
        })

    except Exception as e:
        logger.error(f"获取进化推演状态失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@evolution_bp.route('/<evolution_id>/round/<int:round_num>', methods=['GET'])
def get_evolution_round(evolution_id: str, round_num: int):
    """获取特定轮次数据"""
    try:
        evolution = EvolutionManager.get(evolution_id)
        if not evolution:
            return jsonify({'success': False, 'message': '推演不存在'}), 404

        for r in evolution.rounds:
            if r.round_number == round_num:
                return jsonify({'success': True, 'round': r.to_dict()})

        return jsonify({'success': False, 'message': '轮次不存在'}), 404

    except Exception as e:
        logger.error(f"获取进化轮次失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
