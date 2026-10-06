"""运行配置：全部来自环境变量，凭证绝不写入版本库。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class Config:
    """进程内一次读取；测试通过环境变量注入临时目录与内存后端。"""

    def __init__(self) -> None:
        self.storage_backend = os.environ.get("CAPSULE_STORAGE_BACKEND", "feishu")
        self.memory_store_path = os.environ.get("CAPSULE_MEMORY_STORE_PATH", "")
        self.workspace_dir = Path(
            os.environ.get("CAPSULE_WORKSPACE_DIR")
            or (Path(tempfile.gettempdir()) / "capsule-system")
        )
        # 知识页粒度上限：超过即拒绝，防止页面退化成资料仓库
        self.page_max_chars = _int_env("CAPSULE_PAGE_MAX_CHARS", 60000)
        # 证据文字上限
        self.evidence_max_chars = _int_env("CAPSULE_EVIDENCE_MAX_CHARS", 400000)
        # 任务工作区 TTL：超期目录在下一次任意命令开始时清理
        self.task_ttl_hours = _int_env("CAPSULE_TASK_TTL_HOURS", 24)
        # 网页获取限制
        self.web_max_bytes = _int_env("CAPSULE_WEB_MAX_BYTES", 5 * 1024 * 1024)
        self.web_timeout_seconds = _int_env("CAPSULE_WEB_TIMEOUT_SECONDS", 20)
        # PDF 限制
        self.pdf_max_pages = _int_env("CAPSULE_PDF_MAX_PAGES", 500)
        self.pdf_max_bytes = _int_env("CAPSULE_PDF_MAX_BYTES", 100 * 1024 * 1024)
        # 音频与视频输入的体积上限
        self.media_max_bytes = _int_env("CAPSULE_MEDIA_MAX_BYTES", 500 * 1024 * 1024)
        # 图片限制（像素总数）
        self.image_max_pixels = _int_env("CAPSULE_IMAGE_MAX_PIXELS", 40_000_000)
        # 飞书
        self.lark_cli = os.environ.get("CAPSULE_LARK_CLI", "lark-cli")
        self.feishu_wiki_space_id = os.environ.get("CAPSULE_FEISHU_WIKI_SPACE_ID", "")
        self.feishu_base_app_token = os.environ.get("CAPSULE_FEISHU_BASE_APP_TOKEN", "")
        self.feishu_drive_root_folder_id = os.environ.get(
            "CAPSULE_FEISHU_DRIVE_ROOT_FOLDER_ID", "")
        self.feishu_user_id = os.environ.get("CAPSULE_FEISHU_USER_ID", "")


def get_config() -> Config:
    return Config()
