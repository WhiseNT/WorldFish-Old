"""
配置管理
统一从项目根目录的 .env 文件加载配置
"""

import os
from typing import Any, Dict

from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')


def _load_environment():
    if os.path.exists(project_root_env):
        load_dotenv(project_root_env, override=True)
    else:
        load_dotenv(override=True)


def _serialize_env_value(value: Any) -> str:
    text = str(value)
    escaped = text.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _persist_env_updates(updates: Dict[str, Any]):
    existing_lines = []
    if os.path.exists(project_root_env):
        with open(project_root_env, 'r', encoding='utf-8') as handle:
            existing_lines = handle.readlines()

    found_keys = set()
    new_lines = []

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            new_lines.append(line)
            continue

        key, _ = line.split('=', 1)
        normalized_key = key.strip()
        if normalized_key in updates:
            new_lines.append(f"{normalized_key}={_serialize_env_value(updates[normalized_key])}\n")
            found_keys.add(normalized_key)
        else:
            new_lines.append(line)

    if new_lines and new_lines[-1] and not new_lines[-1].endswith('\n'):
        new_lines[-1] += '\n'

    for key, value in updates.items():
        if key not in found_keys:
            new_lines.append(f"{key}={_serialize_env_value(value)}\n")

    with open(project_root_env, 'w', encoding='utf-8') as handle:
        handle.writelines(new_lines)


_load_environment()


class Config:
    """Flask配置类"""
    
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示（而不是 \uXXXX 格式）
    JSON_AS_ASCII = False
    
    # LLM配置（统一使用OpenAI格式）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    
    # Zep配置
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    @classmethod
    def reload(cls):
        _load_environment()
        cls.SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
        cls.DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
        cls.JSON_AS_ASCII = False
        cls.LLM_API_KEY = os.environ.get('LLM_API_KEY')
        cls.LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
        cls.LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
        cls.ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
        cls.OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
        cls.REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
        cls.REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
        cls.REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    @classmethod
    def mask_secret(cls, value: Any) -> str:
        if not value:
            return ''
        text = str(value)
        if len(text) <= 8:
            return '*' * len(text)
        return f"{text[:4]}...{text[-4:]}"

    @classmethod
    def get_llm_config_status(cls) -> Dict[str, Any]:
        cls.reload()
        return {
            'api_key_configured': bool(cls.LLM_API_KEY),
            'api_key_masked': cls.mask_secret(cls.LLM_API_KEY),
            'base_url': cls.LLM_BASE_URL,
            'model_name': cls.LLM_MODEL_NAME,
        }

    @classmethod
    def save_llm_config(
        cls,
        api_key: Any = None,
        base_url: Any = None,
        model_name: Any = None,
    ) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}

        if api_key is not None:
            cleaned_key = str(api_key).strip()
            if not cleaned_key:
                raise ValueError('LLM API Key 不能为空')
            updates['LLM_API_KEY'] = cleaned_key

        if base_url is not None:
            updates['LLM_BASE_URL'] = str(base_url).strip() or 'https://api.openai.com/v1'

        if model_name is not None:
            updates['LLM_MODEL_NAME'] = str(model_name).strip() or 'gpt-4o-mini'

        if not updates:
            return cls.get_llm_config_status()

        _persist_env_updates(updates)
        for key, value in updates.items():
            os.environ[key] = str(value)

        cls.reload()
        return cls.get_llm_config_status()
    
    @classmethod
    def validate(cls):
        """验证必要配置"""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置")
        return errors


Config.reload()

