"""领域标识与时间：应用生成的 UUID 是领域身份，飞书 token 只是外部定位符。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id() -> str:
    return uuid.uuid4().hex


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
