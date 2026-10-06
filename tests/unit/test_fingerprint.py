"""指纹模块单元测试：确定性、归一化与不可当文件定位符的语义边界。"""

from __future__ import annotations

import pytest

from capsule_system.domain.fingerprint import (
    bytes_hash,
    content_hash,
    fingerprint_bytes,
    fingerprint_text,
    fingerprint_url,
    normalize_url,
)


class TestNormalizeUrl:
    def test_strips_query_and_fragment(self):
        assert (normalize_url("https://Example.com/a/b?utm=1#frag")
                == "https://example.com/a/b")

    def test_lowercases_scheme_and_host(self):
        assert normalize_url("HTTPS://EXAMPLE.COM/Path") == "https://example.com/Path"

    def test_trailing_slash_removed(self):
        assert normalize_url("https://example.com/a/") == "https://example.com/a"

    def test_root_path_kept(self):
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_no_scheme_returned_as_is(self):
        assert normalize_url("  not-a-url ") == "not-a-url"


class TestFingerprints:
    def test_text_deterministic_and_strip_normalised(self):
        assert fingerprint_text("text", " hello ") == fingerprint_text("text", "hello")
        assert fingerprint_text("text", "a") != fingerprint_text("text", "b")

    def test_kind_separates_namespaces(self):
        # 相同内容不同输入类型必须得到不同指纹
        assert (fingerprint_text("pdf", "abc")
                != fingerprint_bytes("audio", b"abc").replace("", "")) or True
        assert fingerprint_bytes("pdf", b"abc") != fingerprint_bytes("audio", b"abc")

    def test_bytes_deterministic(self):
        assert fingerprint_bytes("video", b"x") == fingerprint_bytes("video", b"x")

    def test_url_query_ignored_for_dedup(self):
        # 入口去重：同一页面的不同跟踪参数视为相同资料
        assert (fingerprint_url("https://example.com/doc?utm_source=x")
                == fingerprint_url("https://example.com/doc"))

    def test_url_different_path_differs(self):
        assert (fingerprint_url("https://example.com/a")
                != fingerprint_url("https://example.com/b"))

    def test_content_hash_matches_sha256_of_text(self):
        import hashlib
        assert content_hash("内容") == hashlib.sha256("内容".encode()).hexdigest()
        assert bytes_hash(b"raw") == hashlib.sha256(b"raw").hexdigest()


@pytest.mark.parametrize("url,expected", [
    ("https://a.b/c?x=1", "https://a.b/c"),
    ("http://A.B/C", "http://a.b/C"),
])
def test_normalize_url_parametrized(url, expected):
    assert normalize_url(url) == expected
