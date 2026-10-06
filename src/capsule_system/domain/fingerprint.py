"""入口去重指纹：用于拦截确定性重复，不能作为文件定位符。"""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse, urlunparse


def normalize_url(url: str) -> str:
    """归一化 URL：去掉 query/fragment，scheme/host 小写，用于确定性比较。"""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.hostname:
        return url.strip()
    return urlunparse((parsed.scheme.lower(), parsed.hostname.lower(),
                       parsed.path.rstrip("/") or "/", "", "", ""))


def fingerprint_text(kind: str, text: str) -> str:
    digest = hashlib.sha256()
    digest.update(kind.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(text.strip().encode("utf-8"))
    return digest.hexdigest()


def fingerprint_bytes(kind: str, data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(kind.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(data)
    return digest.hexdigest()


def fingerprint_url(url: str) -> str:
    return fingerprint_text("web_url", normalize_url(url))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
