"""内存后端单元测试：持久化往返、修订计数与无附件约束。"""

from __future__ import annotations

import json

from capsule_system.domain import ids
from capsule_system.domain.entities import KnowledgePageRecord
from capsule_system.storage.memory_backend import InMemoryBackend


def make_page(page_id: str, revision: str = "rev-1") -> KnowledgePageRecord:
    return KnowledgePageRecord(
        page_id=page_id, page_type="topic", title=f"页面{page_id}",
        revision_id=revision, content_hash="h",
        created_at=ids.now_utc(), updated_at=ids.now_utc())


def test_bootstrap_reports_tables_and_no_attachments():
    created = InMemoryBackend().bootstrap("acceptance")
    assert created["branches"] == ["主题", "概念", "证据库"]
    assert created["tables"] == 11
    assert created["attachment_fields"] == 0


def test_persist_roundtrip(tmp_path):
    store = tmp_path / "store.json"
    backend = InMemoryBackend(store)
    page = make_page("p1")
    backend.save_page(page)
    backend.update_page_content("p1", "rev-1", "# 内容", "hash-1")

    reloaded = InMemoryBackend(store)
    assert reloaded.get_page("p1").title == page.title
    assert reloaded.read_page_content("p1") == "# 内容"


def test_revision_counter_survives_reload(tmp_path):
    # 跨进程（多次 CLI 调用）加载后新修订号不能回退
    store = tmp_path / "store.json"
    backend = InMemoryBackend(store)
    backend.save_page(make_page("p1"))
    backend.update_page_content("p1", "rev-1", "# v1", "h1")
    first_revision = backend.get_page("p1").revision_id

    reloaded = InMemoryBackend(store)
    updated = reloaded.update_page_content("p1", first_revision, "# v2", "h2")
    assert updated.revision_id != first_revision
    assert int(updated.revision_id.split("-")[1]) > int(first_revision.split("-")[1])


def test_persisted_store_contains_only_text_and_url_data(tmp_path):
    store = tmp_path / "store.json"
    backend = InMemoryBackend(store)
    backend.save_page(make_page("p1"))
    backend.update_page_content("p1", "rev-1", "# 文字内容", "h")
    raw = json.loads(store.read_text(encoding="utf-8"))
    # 不存在任何附件/二进制字段
    assert set(raw) == {
        "submissions", "original_evidence", "derived_evidence",
        "evidence_contents", "pages", "page_contents", "contributions",
        "processing_records", "understandings", "review_items",
        "review_decisions", "rule_candidates", "rule_candidate_cases",
    }
    assert raw["page_contents"]["p1"] == "# 文字内容"


def test_find_submission_by_fingerprint_roundtrip():
    from capsule_system.domain.entities import Submission
    backend = InMemoryBackend()
    now = ids.now_utc()
    backend.save_submission(Submission(
        submission_id="s1", input_kind="text", status="completed",
        content_fingerprint="fp-1", created_at=now, updated_at=now))
    assert backend.find_submission_by_fingerprint("fp-1").submission_id == "s1"
    assert backend.find_submission_by_fingerprint("fp-2") is None
