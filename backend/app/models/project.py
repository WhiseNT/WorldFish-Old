"""项目模型与持久化仓库。"""

import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from werkzeug.utils import secure_filename

from ..config import Config


class ProjectStatus(str, Enum):
    """项目状态。"""

    CREATED = "created"
    ONTOLOGY_GENERATED = "ontology_generated"
    GRAPH_BUILDING = "graph_building"
    GRAPH_COMPLETED = "graph_completed"
    FAILED = "failed"

    @classmethod
    def from_value(cls, value: Any) -> "ProjectStatus":
        if isinstance(value, cls):
            return value
        if not value:
            return cls.CREATED
        return cls(str(value))


class Project:
    """推演项目。"""

    _KNOWN_FIELDS = {
        "project_id",
        "name",
        "description",
        "world_id",
        "settings",
        "status",
        "files",
        "total_text_length",
        "simulation_requirement",
        "ontology",
        "analysis_summary",
        "graph_id",
        "graph_build_task_id",
        "chunk_size",
        "chunk_overlap",
        "error",
        "created_at",
        "updated_at",
    }

    def __init__(
        self,
        project_id: str,
        name: str,
        description: str = "",
        world_id: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        status: ProjectStatus = ProjectStatus.CREATED,
        files: Optional[List[Dict[str, Any]]] = None,
        total_text_length: int = 0,
        simulation_requirement: str = "",
        ontology: Optional[Dict[str, Any]] = None,
        analysis_summary: str = "",
        graph_id: Optional[str] = None,
        graph_build_task_id: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        error: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        **extra_fields: Any,
    ):
        now = datetime.now().isoformat()
        self.project_id = project_id
        self.name = name
        self.description = description or ""
        self.world_id = world_id
        self.settings = settings or {}
        self.status = ProjectStatus.from_value(status)
        self.files = files or []
        self.total_text_length = total_text_length or 0
        self.simulation_requirement = simulation_requirement or ""
        self.ontology = ontology
        self.analysis_summary = analysis_summary or ""
        self.graph_id = graph_id
        self.graph_build_task_id = graph_build_task_id
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.error = error
        self.created_at = created_at or now
        self.updated_at = updated_at or now

        for key, value in extra_fields.items():
            setattr(self, key, value)

    @property
    def id(self) -> str:
        return self.project_id

    @classmethod
    def create(
        cls,
        name: str,
        description: str = "",
        world_id: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> "Project":
        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        return cls(
            project_id=project_id,
            name=name,
            description=description,
            world_id=world_id,
            settings=settings,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        payload = dict(data or {})
        project_id = payload.pop("project_id", None) or payload.pop("id", None)
        if not project_id:
            raise ValueError("项目数据缺少 project_id")

        payload.pop("id", None)

        extra_fields = {
            key: value
            for key, value in payload.items()
            if key not in cls._KNOWN_FIELDS
        }

        return cls(
            project_id=project_id,
            name=payload.get("name", ""),
            description=payload.get("description", ""),
            world_id=payload.get("world_id"),
            settings=payload.get("settings") or {},
            status=payload.get("status", ProjectStatus.CREATED),
            files=payload.get("files") or [],
            total_text_length=payload.get("total_text_length", 0),
            simulation_requirement=payload.get("simulation_requirement", ""),
            ontology=payload.get("ontology"),
            analysis_summary=payload.get("analysis_summary", ""),
            graph_id=payload.get("graph_id"),
            graph_build_task_id=payload.get("graph_build_task_id"),
            chunk_size=payload.get("chunk_size"),
            chunk_overlap=payload.get("chunk_overlap"),
            error=payload.get("error"),
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
            **extra_fields,
        )

    @classmethod
    def get_by_id(cls, project_id: str) -> Optional["Project"]:
        return ProjectManager.get_project(project_id)

    @classmethod
    def get_all(cls) -> List["Project"]:
        return ProjectManager.list_projects()

    def update(self, data: Dict[str, Any]):
        for key, value in (data or {}).items():
            if key in {"id", "project_id", "created_at"}:
                continue
            if key == "status":
                value = ProjectStatus.from_value(value)
            setattr(self, key, value)
        self.updated_at = datetime.now().isoformat()

    def delete(self) -> bool:
        return ProjectManager.delete_project(self.project_id)

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "id": self.project_id,
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "world_id": self.world_id,
            "settings": self.settings,
            "status": self.status.value,
            "files": self.files,
            "total_text_length": self.total_text_length,
            "simulation_requirement": self.simulation_requirement,
            "ontology": self.ontology,
            "analysis_summary": self.analysis_summary,
            "graph_id": self.graph_id,
            "graph_build_task_id": self.graph_build_task_id,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

        for key, value in self.__dict__.items():
            if key in data or key == "status":
                continue
            if isinstance(value, Enum):
                data[key] = value.value
            else:
                data[key] = value

        return data


class ProjectManager:
    """文件持久化项目仓库。"""

    PROJECTS_DIR = os.path.join(Config.UPLOAD_FOLDER, "projects")
    _lock = threading.RLock()

    @classmethod
    def _ensure_base_dir(cls):
        os.makedirs(cls.PROJECTS_DIR, exist_ok=True)

    @classmethod
    def _project_dir(cls, project_id: str) -> str:
        cls._ensure_base_dir()
        return os.path.join(cls.PROJECTS_DIR, project_id)

    @classmethod
    def _project_file(cls, project_id: str) -> str:
        return os.path.join(cls._project_dir(project_id), "project.json")

    @classmethod
    def _extracted_text_file(cls, project_id: str) -> str:
        return os.path.join(cls._project_dir(project_id), "extracted_text.txt")

    @classmethod
    def _files_dir(cls, project_id: str) -> str:
        return os.path.join(cls._project_dir(project_id), "files")

    @classmethod
    def create_project(
        cls,
        name: str,
        description: str = "",
        world_id: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> Project:
        with cls._lock:
            project = Project.create(
                name=name,
                description=description,
                world_id=world_id,
                settings=settings,
            )
            cls.save_project(project)
            return project

    @classmethod
    def save_project(cls, project: Project) -> Project:
        with cls._lock:
            project_dir = cls._project_dir(project.project_id)
            os.makedirs(project_dir, exist_ok=True)
            project.updated_at = datetime.now().isoformat()
            with open(cls._project_file(project.project_id), "w", encoding="utf-8") as handle:
                json.dump(project.to_dict(), handle, ensure_ascii=False, indent=2)
            return project

    @classmethod
    def get_project(cls, project_id: str) -> Optional[Project]:
        project_file = cls._project_file(project_id)
        if not os.path.exists(project_file):
            return None
        with cls._lock:
            with open(project_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return Project.from_dict(payload)

    @classmethod
    def list_projects(cls, limit: int = 50, world_id: Optional[str] = None) -> List[Project]:
        cls._ensure_base_dir()
        projects: List[Project] = []
        with cls._lock:
            for entry in os.scandir(cls.PROJECTS_DIR):
                if not entry.is_dir():
                    continue
                project = cls.get_project(entry.name)
                if project:
                    projects.append(project)

        if world_id:
            projects = [project for project in projects if project.world_id == world_id]

        projects.sort(key=lambda item: item.updated_at, reverse=True)
        return projects[:limit] if limit else projects

    @classmethod
    def update_project(cls, project_id: str, data: Dict[str, Any]) -> Optional[Project]:
        with cls._lock:
            project = cls.get_project(project_id)
            if not project:
                return None
            project.update(data)
            return cls.save_project(project)

    @classmethod
    def delete_project(cls, project_id: str) -> bool:
        with cls._lock:
            project_dir = cls._project_dir(project_id)
            if not os.path.exists(project_dir):
                return False
            shutil.rmtree(project_dir)
            return True

    @classmethod
    def save_extracted_text(cls, project_id: str, text: str):
        if not cls.get_project(project_id):
            raise ValueError(f"项目不存在: {project_id}")
        with cls._lock:
            os.makedirs(cls._project_dir(project_id), exist_ok=True)
            with open(cls._extracted_text_file(project_id), "w", encoding="utf-8") as handle:
                handle.write(text or "")

    @classmethod
    def get_extracted_text(cls, project_id: str) -> Optional[str]:
        path = cls._extracted_text_file(project_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    @classmethod
    def save_file_to_project(cls, project_id: str, file_storage: Any, original_filename: str) -> Dict[str, Any]:
        if not cls.get_project(project_id):
            raise ValueError(f"项目不存在: {project_id}")

        safe_name = secure_filename(original_filename or "")
        if not safe_name:
            safe_name = f"file_{uuid.uuid4().hex}"

        file_id = uuid.uuid4().hex[:8]
        target_name = f"{file_id}_{safe_name}"

        with cls._lock:
            files_dir = cls._files_dir(project_id)
            os.makedirs(files_dir, exist_ok=True)
            target_path = os.path.join(files_dir, target_name)
            file_storage.save(target_path)

        return {
            "path": target_path,
            "filename": target_name,
            "original_filename": original_filename,
            "size": os.path.getsize(target_path),
        }
