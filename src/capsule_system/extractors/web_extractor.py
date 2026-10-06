"""网页获取与正文抽取：受限 httpx 流式请求 + trafilatura 正文提取
+ 确定性图片候选检测。

网页的"回到原始来源路径"就是用户提交的 URL（FR-005）；
不保存网页截图或网页图片作为载体（research.md §10）。
仅 JavaScript 渲染、需要登录或抽取结果为空的网页进入审核。

图片候选检测（FR-013/014）只做结构性判断，不做语义判断：
- 带 figcaption 的 figure 内图片，或 alt 文本具有说明性的图片，
  视为"可能携带正文无法替代的信息"的候选；
- 纯装饰（logo/icon/avatar 等 alt、data: 内嵌小图、跟踪像素）
  不进入候选；
- 每张候选由 Agent 在 understand_web_images 动作中逐张决定
  忽略或转换为文字，本模块不代替该判断。
"""

from __future__ import annotations

import httpx
import lxml.html
import trafilatura

from ..contracts.envelope import DEPENDENCY_UNAVAILABLE, VALIDATION_ERROR, CapsuleError
from .base import ExtractionResult

USER_AGENT = "capsule-system/0.1 (+personal knowledge archive)"

DECORATIVE_ALT = {
    "", "logo", "icon", "avatar", "spacer", "decoration", "decorative",
    "banner", "advertisement", "ad", "ads", "bullet", "arrow", "divider",
}
MAX_IMAGE_CANDIDATES = 20


def extract_web(url: str, max_bytes: int, timeout_seconds: int,
                max_chars: int) -> ExtractionResult:
    if not url.startswith(("http://", "https://")):
        raise CapsuleError(VALIDATION_ERROR, f"非法 URL：{url}")
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout_seconds,
                          headers={"User-Agent": USER_AGENT}) as client:
            downloaded = 0
            chunks: list[bytes] = []
            with client.stream("GET", url) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise CapsuleError(
                            VALIDATION_ERROR,
                            f"网页体积超过上限 {max_bytes} 字节")
                    chunks.append(chunk)
        html = b"".join(chunks).decode("utf-8", errors="replace")
    except httpx.HTTPStatusError as exc:
        raise CapsuleError(
            DEPENDENCY_UNAVAILABLE, f"网页返回错误状态：{exc.response.status_code}",
            suggested_action="确认 URL 可公开访问后重新提交") from exc
    except httpx.HTTPError as exc:
        raise CapsuleError(
            DEPENDENCY_UNAVAILABLE, f"网页获取失败：{exc}",
            suggested_action="确认 URL 可访问后重新提交") from exc
    extracted = trafilatura.extract(
        html, include_tables=True, include_images=False, favor_recall=True) or ""
    text = extracted.strip()
    if not text:
        # 不引入浏览器运行环境：核心内容无法识别时交由人工审核
        return ExtractionResult(
            text="", complete=False,
            omissions=["网页正文抽取结果为空（可能仅 JavaScript 渲染或需要登录），"
                       "未补写内容"],
            quality={"kind": "web_url", "bytes": downloaded, "empty": True})
    if len(text) > max_chars:
        raise CapsuleError(
            VALIDATION_ERROR, f"网页正文超过上限 {max_chars} 字符",
            suggested_action="分段提交")
    candidates = detect_image_candidates(html)
    omissions = []
    if not candidates:
        omissions.append("网页中没有检测到可能携带独立信息的图片候选")
    return ExtractionResult(
        text=text, complete=True, image_candidates=candidates,
        omissions=omissions,
        quality={"kind": "web_url", "bytes": downloaded, "chars": len(text),
                 "image_candidates": len(candidates)})


def detect_image_candidates(html: str) -> list[dict]:
    """结构性检测可能携带独立信息的图片，返回候选列表。

    每个候选：{"key": 稳定标识, "description": 图注或 alt 文本,
    "src": 图片地址（只作展示参考，系统不保存该 URL 为证据）}。
    """
    try:
        doc = lxml.html.fromstring(html)
    except Exception:  # 解析失败时保守返回空列表，不影响正文处理
        return []
    candidates: list[dict] = []
    seen_src: set[str] = set()

    def _is_decorative(img, empty_alt_ok: bool = False) -> bool:
        alt = (img.get("alt") or "").strip().lower()
        if alt in DECORATIVE_ALT and not (empty_alt_ok and alt == ""):
            return True
        src = (img.get("src") or "").strip()
        if not src or src.startswith("data:"):
            return True
        # 跟踪像素：显式声明极小尺寸的图片
        for dim in (img.get("width"), img.get("height")):
            if dim is not None and dim.strip().isdigit() and int(dim) <= 32:
                return True
        return False

    # 1. figure + figcaption：图注强烈暗示图片携带独立信息。
    #    figure 内图片常无 alt（描述由 figcaption 承担），
    #    只要存在图注，空 alt 不视为装饰性。
    for index, figure in enumerate(doc.iter("figure")):
        img = figure.find(".//img")
        caption = (figure.findtext("figcaption") or "").strip()
        if img is None or _is_decorative(img, empty_alt_ok=bool(caption)):
            continue
        alt = (img.get("alt") or "").strip()
        description = caption or alt
        if not description:
            continue
        src = (img.get("src") or "").strip()
        if src in seen_src:
            continue
        seen_src.add(src)
        candidates.append({"key": f"figure-{index}", "description": description,
                           "src": src})
        if len(candidates) >= MAX_IMAGE_CANDIDATES:
            return candidates

    # 2. 说明性 alt 的独立图片
    for index, img in enumerate(doc.iter("img")):
        if _is_decorative(img):
            continue
        alt = (img.get("alt") or "").strip()
        if len(alt) < 4:  # 过短 alt 视为装饰性标记
            continue
        src = (img.get("src") or "").strip()
        if src in seen_src:
            continue
        seen_src.add(src)
        candidates.append({"key": f"img-{index}", "description": alt,
                           "src": src})
        if len(candidates) >= MAX_IMAGE_CANDIDATES:
            break
    return candidates
