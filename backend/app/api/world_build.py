"""世界观构建 API。"""

import copy
import os
import uuid
import threading

from flask import request, jsonify

from . import world_build_bp
from app.config import Config
from app.models.world import WorldManager
from app.models.map import (
    add_change_record,
    apply_batch_update,
    apply_cell_update,
    cell_neighbors,
    find_cell,
    find_map,
    list_maps as list_structured_maps,
    map_id as create_map_id,
    normalize_map,
    save_maps,
    search_cells,
    stats_for_map,
    summarize_map,
)
from app.services.enhanced_world_extractor import EnhancedWorldExtractor
from app.utils.file_parser import FileParser
from app.utils.llm_client import LLMClient
from app.utils.logger import get_logger
logger = get_logger('mirofish.api.world_build')

# 提取任务进度存储
_extraction_tasks = {}
_tasks_lock = threading.Lock()


def allowed_file(filename: str) -> bool:
    """检查文件扩展名是否允许"""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


def _ensure_upload_dir():
    """确保上传目录存在"""
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)


@world_build_bp.route('/create', methods=['POST'])
def create_world():
    """创建新的世界观"""
    try:
        data = request.json or {}
        world = WorldManager.create_world(
            name=data.get('name'),
            description=data.get('description', ''),
            era=data.get('era', ''),
            anchor_time=data.get('anchor_time', ''),
            settings=data.get('settings', {}),
            writing_style=data.get('writing_style', ''),
            reference_text=data.get('reference_text', '')
        )
        return jsonify({
            'success': True,
            'world_id': world.id,
            'world': world.to_dict(),
            'message': '世界观创建成功'
        })
    except Exception as e:
        logger.error(f"创建世界观失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建失败: {str(e)}'
        }), 500


@world_build_bp.route('/extract', methods=['POST'])
def extract_world():
    """从文本或上传文件提取世界观信息"""
    try:
        text = None
        uploaded_filenames = []
        direct_json_data = None

        # 处理文件上传（multipart/form-data）
        uploaded_files = request.files.getlist('files') if request.files else []
        logger.info(f"提取请求: files={len(uploaded_files)}, form_keys={list(request.form.keys())}, content_type={request.content_type}")

        if uploaded_files:
            _ensure_upload_dir()
            text_parts = []
            json_parts = []
            failed_files = []
            for file in uploaded_files:
                if not file or not file.filename:
                    continue
                filename = file.filename
                ext = os.path.splitext(filename)[1].lower().lstrip('.')
                if ext not in Config.ALLOWED_EXTENSIONS and ext != 'json':
                    logger.warning(f"不支持的文件格式: {filename} ({ext})")
                    continue

                import uuid
                import time
                safe_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{filename}"
                file_path = os.path.join(Config.UPLOAD_FOLDER, safe_name)
                file.save(file_path)
                logger.info(f"文件已保存: {filename} ({os.path.getsize(file_path)} bytes)")
                try:
                    if ext == 'json':
                        import json as json_module
                        with open(file_path, 'r', encoding='utf-8') as f:
                            json_data = json_module.load(f)
                        if isinstance(json_data, dict):
                            json_parts.append(json_data)
                            uploaded_filenames.append(filename)
                    else:
                        extracted_text = FileParser.extract_text(file_path)
                        logger.info(f"文本提取成功: {filename} ({len(extracted_text)} 字符)")
                        text_parts.append(f"=== {filename} ===\n{extracted_text}")
                        uploaded_filenames.append(filename)
                except Exception as file_err:
                    logger.error(f"文件处理失败 {filename}: {file_err}")
                    failed_files.append(f"{filename}: {str(file_err)}")
                finally:
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass

            if text_parts:
                text = "\n\n".join(text_parts)
                logger.info(f"合并文本: {len(text_parts)} 个文件, 总计 {len(text)} 字符")

            if failed_files and not text_parts:
                return jsonify({
                    'success': False,
                    'message': f'所有文件处理失败: {"; ".join(failed_files)}'
                }), 400

            # 合并多个 JSON 文件
            if json_parts:
                direct_json_data = json_parts[0]
                for extra in json_parts[1:]:
                    if extra.get('entities'):
                        direct_json_data.setdefault('entities', []).extend(extra['entities'])
                    if extra.get('events'):
                        direct_json_data.setdefault('events', []).extend(extra['events'])
                    if extra.get('settings') and isinstance(extra['settings'], dict):
                        existing_settings = direct_json_data.setdefault('settings', {})
                        if isinstance(existing_settings, dict):
                            existing_settings.setdefault('items', []).extend(
                                extra['settings'].get('items') or [])
                            existing_settings.setdefault('calendars', []).extend(
                                extra['settings'].get('calendars') or [])

            # 也检查 form 中的 text 字段
            form_text = request.form.get('text', '').strip()
            if form_text:
                text = (text + "\n\n" + form_text) if text else form_text

        # 普通 JSON 请求
        if not text and direct_json_data is None:
            data = request.get_json(silent=True) or {}
            text = data.get('text', '').strip()
            # 支持直接 POST JSON 数据
            if not text and isinstance(data, dict) and (
                'world_info' in data or 'entities' in data or 'events' in data or 'settings' in data
            ):
                direct_json_data = data

        if not text and direct_json_data is None:
            logger.warning(f"提取请求无有效内容: has_files={len(uploaded_files) > 0}, has_text={bool(text)}, has_json={direct_json_data is not None}")
            return jsonify({
                'success': False,
                'message': '请提供文本内容或上传文件（支持 PDF、Markdown、TXT、JSON 格式）'
            }), 400

        # 如果只有 JSON 数据没有文本，直接返回，无需 LLM
        if direct_json_data is not None and not text:
            resp = {
                'success': True,
                'extracted_data': direct_json_data
            }
            if uploaded_filenames:
                resp['source_files'] = uploaded_filenames
            return jsonify(resp)

        # 有文本内容，需要 LLM 提取
        if not Config.get_llm_config('parser').get('api_key'):
            return jsonify({
                'success': False,
                'message': 'LLM API Key 未配置，请至少配置 Agent/SubAgent/解析 Agent 中的一组 LLM API。'
            }), 400

        # 捕获请求数据供后台线程使用（JSON 或 form 中的 world_id）
        request_data = request.get_json(silent=True) or {}
        if not isinstance(request_data, dict):
            request_data = {}
        # 也合并 form 数据
        if request.form:
            request_data.update(dict(request.form))

        # 启动后台提取任务，立即返回 task_id
        task_id = f"extract_{uuid.uuid4().hex[:12]}"
        with _tasks_lock:
            _extraction_tasks[task_id] = {
                'stage': 'starting',
                'progress': 0,
                'message': '正在启动提取...',
                'done': False,
                'result': None,
                'error': None,
            }

        def run_extraction():
            _rag_result = [None]
            _rag_thread = None

            try:
                def progress_cb(stage, progress, message, detail=None):
                    with _tasks_lock:
                        if task_id in _extraction_tasks:
                            _extraction_tasks[task_id].update({
                                'stage': stage,
                                'progress': max(0, min(int(progress), 100)),
                                'message': message,
                                'detail': detail or {},
                            })

                def rag_progress_cb(stage, progress, message, detail=None):
                    rag_payload = {
                        'stage': stage,
                        'progress': max(0, min(int(progress), 100)),
                        'message': message,
                        'detail': detail or {},
                    }
                    with _tasks_lock:
                        if task_id in _extraction_tasks:
                            task = _extraction_tasks[task_id]
                            task['rag_progress'] = rag_payload
                            # RAG 在后台并行，不直接覆盖 LLM 主进度；但在索引收尾阶段透传为主提示。
                            if task.get('stage') == 'indexing' or progress >= 90:
                                task.update({
                                    'stage': 'indexing',
                                    'progress': max(int(task.get('progress', 0)), min(95, 60 + int(progress * 0.35))),
                                    'message': f'向量索引：{message}',
                                    'detail': {'rag_progress': rag_payload},
                                })

                progress_cb('preparing', 5, '正在初始化...')

                # ── 启动 RAG 索引（与 LLM 提取并行）──
                world_id = request_data.get('world_id', '').strip()
                emb_config = Config.get_embedding_config()
                if world_id and text and emb_config.get('api_key'):
                    def _do_rag():
                        try:
                            from ..services.rag_service import RagService
                            rag = RagService(world_id)
                            doc_ids = rag.add_text_chunks(
                                text=text,
                                source="extraction",
                                metadata={"filename": ", ".join(uploaded_filenames) if uploaded_filenames else "direct_text"},
                                chunk_preset="novel",
                                progress_callback=rag_progress_cb,
                            )
                            stats = rag.get_stats()
                            _rag_result[0] = {
                                'rag_indexed': True,
                                'rag_added_count': len(doc_ids),
                                'rag_document_count': stats.get('document_count', 0),
                            }
                            logger.info(f"RAG 并行索引完成 [{world_id}]: 新增 {len(doc_ids)} 块，总计 {stats.get('document_count', 0)} 文档"
                                        f" (来源: {uploaded_filenames or '直接输入'})")
                        except Exception as rag_err:
                            logger.warning(f"RAG 并行索引失败 [{world_id}]: {rag_err}", exc_info=True)
                            _rag_result[0] = {'rag_indexed': False, 'rag_error': str(rag_err)}
                    _rag_thread = threading.Thread(target=_do_rag, daemon=True)
                    _rag_thread.start()
                    progress_cb('indexing', 7, '正在并行执行 LLM 提取与小说章节向量索引...', {'rag_progress': {'stage': 'queued', 'progress': 0, 'message': 'RAG 索引已排队'}})
                elif not world_id:
                    logger.info("RAG 索引跳过: 未提供 world_id")
                elif not emb_config.get('api_key'):
                    logger.warning("RAG 索引跳过: 未配置 Embedding/LLM API Key")

                extractor = EnhancedWorldExtractor()

                text_len = len(text) if text else 0
                if text_len > extractor.LONG_TEXT_THRESHOLD:
                    progress_cb('chunking', 8, f'文本 {text_len} 字符，正在章节感知切分...')

                # LLM 提取（与 RAG 并行进行中）
                result = extractor.extract_from_text(text, progress_callback=progress_cb)

                # 合并直接导入的 JSON 数据
                if direct_json_data is not None:
                    if direct_json_data.get('entities'):
                        result.setdefault('entities', []).extend(direct_json_data['entities'])
                    if direct_json_data.get('events'):
                        result.setdefault('events', []).extend(direct_json_data['events'])
                    if direct_json_data.get('settings', {}).get('items'):
                        result.setdefault('settings', {}).setdefault('items', []).extend(
                            direct_json_data['settings']['items'])
                    if direct_json_data.get('settings', {}).get('calendars'):
                        result.setdefault('settings', {}).setdefault('calendars', []).extend(
                            direct_json_data['settings']['calendars'])

                # 等待 RAG 索引线程（通常已提前完成）
                if _rag_thread and _rag_thread.is_alive():
                    progress_cb('indexing', 93, '正在等待向量索引收尾...')
                    _rag_thread.join()
                    import time as _time
                    _time.sleep(0.3)  # 让前端看到收尾状态
                if _rag_result[0]:
                    result.update(_rag_result[0])

                with _tasks_lock:
                    _extraction_tasks[task_id].update({
                        'stage': 'done', 'progress': 100,
                        'message': f'提取完成: {len(result.get("entities", []))} 实体, {len(result.get("events", []))} 事件',
                        'done': True, 'result': result,
                    })
            except Exception as e:
                logger.error(f"后台提取失败 [{task_id}]: {e}")
                with _tasks_lock:
                    _extraction_tasks[task_id].update({
                        'stage': 'error', 'done': True,
                        'error': str(e),
                        'message': f'提取失败: {str(e)[:100]}',
                    })

        threading.Thread(target=run_extraction, daemon=True).start()

        resp = {
            'success': True,
            'task_id': task_id,
            'message': '提取任务已启动',
        }
        if uploaded_filenames:
            resp['source_files'] = uploaded_filenames
        return jsonify(resp)
    except Exception as e:
        logger.error(f"提取世界观失败: {str(e)}")
        error_text = str(e).lower()
        status_code = 400 if any(keyword in error_text for keyword in ['llm', 'api key', 'api_key', 'invalid_api_key']) else 500
        return jsonify({
            'success': False,
            'message': f'提取失败: {str(e)}'
        }), status_code


@world_build_bp.route('/extract/<task_id>/progress', methods=['GET'])
def extract_progress(task_id):
    """轮询提取任务进度"""
    with _tasks_lock:
        task = _extraction_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'message': '任务不存在或已过期'}), 404
    resp = {
        'success': True,
        'task_id': task_id,
        'stage': task['stage'],
        'progress': task['progress'],
        'message': task['message'],
        'detail': task.get('detail') or {},
        'rag_progress': task.get('rag_progress'),
        'done': task['done'],
    }
    if task['done'] and task.get('result'):
        resp['extracted_data'] = task['result']
    if task.get('error'):
        resp['error'] = task['error']
    return jsonify(resp)


@world_build_bp.route('/llm-config', methods=['GET'])
def get_llm_config():
    """获取当前 LLM 配置状态。"""
    return jsonify({
        'success': True,
        'config': Config.get_llm_config_status()
    })


@world_build_bp.route('/llm-config', methods=['PUT'])
def update_llm_config():
    """保存 LLM 配置。"""
    try:
        data = request.json or {}
        config = Config.save_llm_config(
            api_key=data.get('api_key') if 'api_key' in data else None,
            base_url=data.get('base_url') if 'base_url' in data else None,
            model_name=data.get('model_name') if 'model_name' in data else None,
        )
        return jsonify({
            'success': True,
            'config': config,
            'message': 'LLM 配置已保存'
        })
    except Exception as e:
        logger.error(f"保存 LLM 配置失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'保存失败: {str(e)}'
        }), 400


@world_build_bp.route('/llm-config/test', methods=['POST'])
def test_llm_config():
    """测试当前或传入的 LLM 配置。"""
    try:
        data = request.json or {}

        resolved_llm = Config.get_llm_config('agent')
        api_key = data.get('api_key') or resolved_llm['api_key']
        base_url = data.get('base_url') or resolved_llm['base_url']
        model_name = data.get('model_name') or resolved_llm['model_name']

        if not api_key:
            return jsonify({
                'success': False,
                'message': '请先提供 LLM API Key'
            }), 400

        test_result = LLMClient.test_connection(
            api_key=api_key,
            base_url=base_url,
            model=model_name,
        )

        return jsonify({
            'success': True,
            'message': 'LLM 连接测试成功',
            'config': {
                'api_key_configured': bool(api_key),
                'api_key_masked': Config.mask_secret(api_key),
                'base_url': base_url,
                'model_name': model_name,
            },
            'test_result': test_result,
        })
    except Exception as e:
        logger.error(f"测试 LLM 配置失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'测试失败: {str(e)}'
        }), 400


def _get_world_or_404(world_id):
    world = WorldManager.get_world(world_id)
    if not world:
        return None, (jsonify({'success': False, 'message': '世界观不存在'}), 404)
    return world, None


@world_build_bp.route('/<world_id>/maps', methods=['GET'])
def list_world_maps(world_id):
    """列出世界观下的结构化地图。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        maps = list_structured_maps(world)
        save_maps(world, maps)
        WorldManager.save_world(world)
        return jsonify({'success': True, 'maps': [summarize_map(item) for item in maps]})
    except Exception as e:
        logger.error(f"列出地图失败: {str(e)}")
        return jsonify({'success': False, 'message': f'获取失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/maps', methods=['POST'])
def create_world_map(world_id):
    """创建结构化六边形地图。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        data = request.json or {}
        maps = list_structured_maps(world)
        new_id = create_map_id()
        raw_map = {
            'id': new_id,
            'world_id': world_id,
            'name': data.get('name') or '未命名地图',
            'description': data.get('description') or '',
            'type': data.get('type') or 'world',
            'width': data.get('width') or 12,
            'height': data.get('height') or 8,
            'is_default': bool(data.get('is_default') or not maps),
        }
        created = normalize_map(raw_map, world_id)
        if created.get('is_default'):
            for item in maps:
                item['is_default'] = False
        maps.append(created)
        save_maps(world, maps)
        WorldManager.save_world(world)
        return jsonify({'success': True, 'map': created, 'message': '地图创建成功'})
    except Exception as e:
        logger.error(f"创建地图失败: {str(e)}")
        return jsonify({'success': False, 'message': f'创建失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/maps/<map_id>', methods=['GET'])
def get_world_map(world_id, map_id):
    """获取完整地图。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        maps = list_structured_maps(world)
        target = find_map(maps, map_id)
        if not target:
            return jsonify({'success': False, 'message': '地图不存在'}), 404
        return jsonify({'success': True, 'map': target, 'stats': stats_for_map(target)})
    except Exception as e:
        logger.error(f"获取地图失败: {str(e)}")
        return jsonify({'success': False, 'message': f'获取失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/maps/<map_id>', methods=['PUT'])
def update_world_map(world_id, map_id):
    """更新地图基础信息。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        data = request.json or {}
        maps = list_structured_maps(world)
        target = find_map(maps, map_id)
        if not target:
            return jsonify({'success': False, 'message': '地图不存在'}), 404
        before = copy.deepcopy(summarize_map(target))
        for field in ['name', 'description', 'type']:
            if field in data:
                target[field] = str(data.get(field) or '').strip()
        if 'view' in data and isinstance(data.get('view'), dict):
            target['view'] = {**target.get('view', {}), **data['view']}
        from app.models.map import now_iso
        target['updated_at'] = now_iso()
        add_change_record(target, 'map', map_id, before, summarize_map(target), source=data.get('source') or 'user')
        save_maps(world, maps)
        WorldManager.save_world(world)
        return jsonify({'success': True, 'map': target, 'message': '地图已更新'})
    except Exception as e:
        logger.error(f"更新地图失败: {str(e)}")
        return jsonify({'success': False, 'message': f'更新失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/maps/<map_id>', methods=['DELETE'])
def delete_world_map(world_id, map_id):
    """删除结构化地图。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        maps = list_structured_maps(world)
        remaining = [item for item in maps if item.get('id') != map_id]
        if len(remaining) == len(maps):
            return jsonify({'success': False, 'message': '地图不存在'}), 404
        save_maps(world, remaining)
        WorldManager.save_world(world)
        return jsonify({'success': True, 'message': '地图已删除'})
    except Exception as e:
        logger.error(f"删除地图失败: {str(e)}")
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/maps/<map_id>/duplicate', methods=['POST'])
def duplicate_world_map(world_id, map_id):
    """复制地图。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        maps = list_structured_maps(world)
        target = find_map(maps, map_id)
        if not target:
            return jsonify({'success': False, 'message': '地图不存在'}), 404
        copied = copy.deepcopy(target)
        new_id = create_map_id()
        copied['id'] = new_id
        copied['name'] = f"{target.get('name') or '地图'} 副本"
        copied['is_default'] = False
        for cell in copied.get('cells', []):
            cell['map_id'] = new_id
        copied = normalize_map(copied, world_id)
        maps.append(copied)
        save_maps(world, maps)
        WorldManager.save_world(world)
        return jsonify({'success': True, 'map': copied, 'message': '地图已复制'})
    except Exception as e:
        logger.error(f"复制地图失败: {str(e)}")
        return jsonify({'success': False, 'message': f'复制失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/maps/<map_id>/default', methods=['PUT'])
def set_default_world_map(world_id, map_id):
    """设置默认地图。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        maps = list_structured_maps(world)
        target = find_map(maps, map_id)
        if not target:
            return jsonify({'success': False, 'message': '地图不存在'}), 404
        for item in maps:
            item['is_default'] = item.get('id') == map_id
        save_maps(world, maps)
        WorldManager.save_world(world)
        return jsonify({'success': True, 'maps': [summarize_map(item) for item in maps], 'message': '默认地图已更新'})
    except Exception as e:
        logger.error(f"设置默认地图失败: {str(e)}")
        return jsonify({'success': False, 'message': f'设置失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/maps/<map_id>/cells/<cell_id>', methods=['PUT'])
def update_world_map_cell(world_id, map_id, cell_id):
    """更新地图单元。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        data = request.json or {}
        maps = list_structured_maps(world)
        target_map = find_map(maps, map_id)
        if not target_map:
            return jsonify({'success': False, 'message': '地图不存在'}), 404
        target_cell = find_cell(target_map, cell_id)
        if not target_cell:
            return jsonify({'success': False, 'message': '地图单元不存在'}), 404
        before = copy.deepcopy(target_cell)
        updated = apply_cell_update(target_cell, data)
        target_map['cells'] = [updated if cell.get('id') == cell_id else cell for cell in target_map.get('cells', [])]
        target_map['updated_at'] = updated.get('updated_at')
        add_change_record(target_map, 'cell', cell_id, before, updated, source=data.get('source') or 'user', agent=bool(data.get('is_agent')))
        save_maps(world, maps)
        WorldManager.save_world(world)
        return jsonify({'success': True, 'cell': updated, 'message': '地图单元已更新'})
    except Exception as e:
        logger.error(f"更新地图单元失败: {str(e)}")
        return jsonify({'success': False, 'message': f'更新失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/maps/<map_id>/cells/batch', methods=['POST'])
def batch_update_world_map_cells(world_id, map_id):
    """批量更新地图单元。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        data = request.json or {}
        cell_ids = set(data.get('cell_ids') or [])
        if not cell_ids:
            return jsonify({'success': False, 'message': '请提供要修改的地图单元'}), 400
        updates = data.get('updates') if isinstance(data.get('updates'), dict) else {}
        maps = list_structured_maps(world)
        target_map = find_map(maps, map_id)
        if not target_map:
            return jsonify({'success': False, 'message': '地图不存在'}), 404
        changed = []
        new_cells = []
        for cell in target_map.get('cells', []):
            if cell.get('id') in cell_ids:
                before = copy.deepcopy(cell)
                updated = apply_batch_update(cell, updates)
                changed.append(updated)
                add_change_record(target_map, 'cell', cell.get('id'), before, updated, source=data.get('source') or 'user', agent=bool(data.get('is_agent')))
                new_cells.append(updated)
            else:
                new_cells.append(cell)
        target_map['cells'] = new_cells
        from app.models.map import now_iso
        target_map['updated_at'] = now_iso()
        save_maps(world, maps)
        WorldManager.save_world(world)
        return jsonify({'success': True, 'updated_count': len(changed), 'cells': changed, 'message': f'已更新 {len(changed)} 个地图单元'})
    except Exception as e:
        logger.error(f"批量更新地图单元失败: {str(e)}")
        return jsonify({'success': False, 'message': f'批量更新失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/maps/<map_id>/search', methods=['GET'])
def search_world_map(world_id, map_id):
    """搜索地图单元。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        query = request.args.get('q', '')
        maps = list_structured_maps(world)
        target_map = find_map(maps, map_id)
        if not target_map:
            return jsonify({'success': False, 'message': '地图不存在'}), 404
        results = search_cells(target_map, query)[:100]
        compact = [{
            'id': cell.get('id'),
            'q': cell.get('q'),
            'r': cell.get('r'),
            'name': cell.get('name'),
            'terrain': cell.get('terrain'),
            'status': cell.get('status'),
            'faction': cell.get('faction'),
            'resources': cell.get('resources') or [],
        } for cell in results]
        return jsonify({'success': True, 'results': compact, 'total': len(results)})
    except Exception as e:
        logger.error(f"搜索地图失败: {str(e)}")
        return jsonify({'success': False, 'message': f'搜索失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/maps/<map_id>/cells/<cell_id>/neighbors', methods=['GET'])
def get_world_map_cell_neighbors(world_id, map_id, cell_id):
    """获取地图单元邻接关系。"""
    try:
        world, error = _get_world_or_404(world_id)
        if error:
            return error
        maps = list_structured_maps(world)
        target_map = find_map(maps, map_id)
        if not target_map:
            return jsonify({'success': False, 'message': '地图不存在'}), 404
        target_cell = find_cell(target_map, cell_id)
        if not target_cell:
            return jsonify({'success': False, 'message': '地图单元不存在'}), 404
        return jsonify({'success': True, 'neighbors': cell_neighbors(target_map, target_cell)})
    except Exception as e:
        logger.error(f"获取邻接单元失败: {str(e)}")
        return jsonify({'success': False, 'message': f'获取失败: {str(e)}'}), 500


@world_build_bp.route('/<world_id>/entities', methods=['POST'])
def add_entity(world_id):
    """添加实体（人物、国家等）"""
    try:
        data = request.json or {}
        entity = WorldManager.add_entity(
            world_id=world_id,
            name=data.get('name'),
            type=data.get('type'),
            attributes=data.get('attributes', {}),
            stages=data.get('stages', []),
            setting_item_id=data.get('setting_item_id', '')
        )
        return jsonify({
            'success': True,
            'entity_id': entity.id,
            'entity': entity.to_dict(),
            'message': '实体添加成功'
        })
    except Exception as e:
        logger.error(f"添加实体失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'添加失败: {str(e)}'
        }), 500


@world_build_bp.route('/<world_id>/events', methods=['POST'])
def add_event(world_id):
    """添加事件"""
    try:
        data = request.json or {}
        event = WorldManager.add_event(
            world_id=world_id,
            name=data.get('name'),
            description=data.get('description'),
            date=data.get('date'),
            entities=data.get('entities', [])
        )
        return jsonify({
            'success': True,
            'event_id': event.id,
            'event': event.to_dict(),
            'message': '事件添加成功'
        })
    except Exception as e:
        logger.error(f"添加事件失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'添加失败: {str(e)}'
        }), 500


@world_build_bp.route('/<world_id>', methods=['PUT'])
def update_world(world_id):
    """更新世界观详情。"""
    try:
        data = request.json or {}
        world = WorldManager.update_world(world_id, data)
        if not world:
            return jsonify({
                'success': False,
                'message': '世界观不存在'
            }), 404

        return jsonify({
            'success': True,
            'world': world.to_dict(),
            'message': '世界观更新成功'
        })
    except Exception as e:
        logger.error(f"更新世界观失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新失败: {str(e)}'
        }), 500


@world_build_bp.route('/list', methods=['GET'])
def list_worlds():
    """列出所有世界观"""
    try:
        worlds = WorldManager.list_worlds()
        return jsonify({
            'success': True,
            'worlds': [
                {
                    'id': w.id,
                    'name': w.name,
                    'description': w.description,
                    'era': w.era,
                    'anchor_time': w.anchor_time,
                    'entities_count': len(w.entities),
                    'events_count': len(w.events),
                    'created_at': w.created_at,
                }
                for w in worlds
            ]
        })
    except Exception as e:
        logger.error(f"列出世界观失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取失败: {str(e)}'
        }), 500


@world_build_bp.route('/<world_id>', methods=['GET'])
def get_world(world_id):
    """获取世界观详情"""
    try:
        world = WorldManager.get_world(world_id)
        if not world:
            return jsonify({
                'success': False,
                'message': '世界观不存在'
            }), 404

        return jsonify({
            'success': True,
            'world': world.to_dict()
        })
    except Exception as e:
        logger.error(f"获取世界观失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取失败: {str(e)}'
        }), 500


@world_build_bp.route('/<world_id>', methods=['DELETE'])
def delete_world(world_id):
    """删除世界观"""
    try:
        deleted = WorldManager.delete_world(world_id)
        if not deleted:
            return jsonify({
                'success': False,
                'message': '世界观不存在'
            }), 404

        return jsonify({
            'success': True,
            'message': '世界观已删除'
        })
    except Exception as e:
        logger.error(f"删除世界观失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除失败: {str(e)}'
        }), 500
