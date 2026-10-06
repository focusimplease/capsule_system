"""飞书适配器：全部飞书能力经 lark-cli 1.0.94 结构化输出调用。

依据 contracts/feishu-storage.md：
- 第一版不混用 lark-cli、原始 HTTP 请求和官方 Python SDK；
- 知识库三分支（主题/概念/证据库）+ 云文档长文本 + 多维表格终态记录；
- 系统不保存任何附件，协议中没有云盘上传/下载方法（FR-006/007）；
- 写入使用读取时取得的版本，写后重新读取核对；
- 契约测试以注入的 runner 模拟 lark-cli；真实资源验证由
  quickstart §7 的 live_feishu 验收执行（需要专用测试空间）。

与 lark-cli 1.0.94 命令面的对应关系（全部经真实 --help 与只读探测核对）：
- 身份：whoami（原始 JSON，无 ok 信封；凭证外部注入的环境不支持 auth 命令）
- 知识库：wiki +node-create / +node-list
- 多维表格表：base +table-list / +table-create / +table-delete
- 多维表格记录：base +record-list（矩阵输出 data.data×fields×record_id_list）
  / +record-batch-create / +record-batch-update / +record-delete
- 云文档：docs +create / +fetch / +update（overwrite + --revision-id 乐观并发）
- 删除：drive +delete --type docx --yes
- 信封：成功 {"ok":true,"data":{...}}；错误 {"ok":false,"error":{...}}（非零退出码）

多维表格的 record_id 由飞书生成（rec 前缀），与领域实体 ID 无关：
实体 ID 保存在普通 text 字段，查找先经 +record-list 过滤定位 record_id。
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

from ...contracts.envelope import (
    DEPENDENCY_UNAVAILABLE,
    NOT_FOUND,
    PARTIAL_WRITE,
    POLICY_BLOCKED,
    RATE_LIMITED,
    STALE_VERSION,
    CapsuleError,
)
from ...domain.entities import (
    DerivedEvidence,
    EvidenceContributionRecord,
    KnowledgePageRecord,
    OriginalEvidence,
    ProcessingRecord,
    ReviewDecisionRecord,
    ReviewItemRecord,
    RuleCandidateCaseRecord,
    RuleCandidateRecord,
    Submission,
    UnderstandingResultRecord,
)
from ...domain import fingerprint as fp

WIKI_BRANCHES = {"topic": "主题", "concept": "概念", "evidence": "证据库"}

# markdown 字面特殊字符（lark-doc markdown 参考：无条件转义），
# 外加 |（防止文字被解析为 GFM 表格）
_MD_ESCAPE_CHARS = set("\\`*_[]$~<|")
# 仅行首需要转义的字符（标题 / 列表 / 引用 / 分隔线）
_MD_LINE_START_ESCAPES = set("#+->")
_MD_UNESCAPABLE = _MD_ESCAPE_CHARS | _MD_LINE_START_ESCAPES


def escape_markdown(text: str) -> str:
    """把字面文本转义为可逐字节往返的 markdown 源（写证据云文档用）。

    依据 lark-cli markdown 格式参考：写入字面特殊字符必须转义，
    否则会被 markdown 语法吞掉（如 _x_ 变斜体、行首 # 变标题并
    被提升为文档标题）；导出侧会做同样的转义，读回时反转义即可
    还原原文。"""
    lines = []
    for line in text.split("\n"):
        escaped = "".join("\\" + ch if ch in _MD_ESCAPE_CHARS else ch
                          for ch in line)
        if line[:1] in _MD_LINE_START_ESCAPES:
            escaped = "\\" + escaped
        lines.append(escaped)
    return "\n".join(lines)


def unescape_markdown(text: str) -> str:
    """还原 escape_markdown / 飞书导出产生的转义（读证据云文档用）。

    只还原确定的转义对（\\ 与 \加可转义字符）；其余字符原样保留，
    因此对不含转义的旧数据（未转义写入）是恒等变换。"""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if (ch == "\\" and i + 1 < len(text)
                and text[i + 1] in _MD_UNESCAPABLE):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def decode_evidence_markdown(text: str) -> str:
    """把飞书 markdown 导出还原为证据原文（读证据云文档用）。

    飞书把段内换行导出为「  \\n」硬换行标记，先统一还原为普通
    换行（段落间的空行不受影响），再反转义写入时的字面特殊字符。
    原文行尾本就带两个空格的罕见情形无法与标记区分，会触发哈希
    核对失败并按契约进入审核。"""
    return unescape_markdown(text.replace("  \n", "\n"))

# 每张表的字段 schema：(Base 字段名, 实体属性名, 值类型)。
# 值类型：text=字符串；json=序列化为 JSON 文本；bool="true"/"false"；int=数字文本。
# 第一个字段是 Base 主字段。字段名是契约稳定名称（contracts/feishu-storage.md）。
TABLE_SCHEMAS: dict[str, list[tuple[str, str, str]]] = {
    "submissions": [
        ("submission_id", "submission_id", "text"),
        ("input_kind", "input_kind", "text"),
        ("source_url", "source_url", "text"),
        ("user_context", "user_context", "text"),
        ("content_fingerprint", "content_fingerprint", "text"),
        ("status", "status", "text"),
        ("created_at", "created_at", "text"),
        ("updated_at", "updated_at", "text"),
    ],
    "original_evidence": [
        ("original_evidence_id", "original_evidence_id", "text"),
        ("submission_id", "submission_id", "text"),
        ("url", "url", "text"),
        ("availability", "availability", "text"),
        ("last_checked_at", "last_checked_at", "text"),
        ("created_at", "created_at", "text"),
        ("updated_at", "updated_at", "text"),
    ],
    "processing_records": [
        ("processing_record_id", "processing_record_id", "text"),
        ("submission_id", "submission_id", "text"),
        ("outcome", "outcome", "text"),
        ("reason_code", "reason_code", "text"),
        ("reason_detail", "reason_detail", "text"),
        ("omissions_json", "omissions", "json"),
        ("evidence_refs_json", "evidence_refs", "json"),
        ("affected_page_refs_json", "affected_page_refs", "json"),
        ("created_at", "created_at", "text"),
    ],
    "evidence_index": [
        ("evidence_id", "evidence_id", "text"),
        ("submission_id", "submission_id", "text"),
        ("original_evidence_id", "original_evidence_id", "text"),
        ("title", "title", "text"),
        ("content_hash", "content_hash", "text"),
        ("preview", "preview", "text"),
        ("docx_token", "docx_token", "text"),
        ("wiki_node_token", "wiki_node_token", "text"),
        ("status", "status", "text"),
        ("supersedes_evidence_id", "supersedes_evidence_id", "text"),
        ("created_at", "created_at", "text"),
    ],
    "knowledge_pages": [
        ("page_id", "page_id", "text"),
        ("page_type", "page_type", "text"),
        ("title", "title", "text"),
        ("docx_token", "docx_token", "text"),
        ("wiki_node_token", "wiki_node_token", "text"),
        ("revision_id", "revision_id", "text"),
        ("content_hash", "content_hash", "text"),
        ("status", "status", "text"),
        ("created_at", "created_at", "text"),
        ("updated_at", "updated_at", "text"),
    ],
    "evidence_contributions": [
        ("contribution_id", "contribution_id", "text"),
        ("evidence_id", "evidence_id", "text"),
        ("page_id", "page_id", "text"),
        ("understanding_id", "understanding_id", "text"),
        ("contribution_kind", "contribution_kind", "text"),
        ("claim_anchors_json", "claim_anchors", "json"),
        ("page_revision_before", "page_revision_before", "text"),
        ("page_revision_after", "page_revision_after", "text"),
        ("summary", "summary", "text"),
        ("created_at", "created_at", "text"),
    ],
    "understanding_results": [
        ("understanding_id", "understanding_id", "text"),
        ("submission_id", "submission_id", "text"),
        ("evidence_id", "evidence_id", "text"),
        ("schema_version", "schema_version", "text"),
        ("topics_json", "topics", "json"),
        ("key_claims_json", "key_claims", "json"),
        ("duplicate_parts_json", "duplicate_parts", "json"),
        ("novel_parts_json", "novel_parts", "json"),
        ("conflicts_json", "conflicts", "json"),
        ("target_pages_json", "target_pages", "json"),
        ("conservative_notes_json", "conservative_notes", "json"),
        ("validation_status", "validation_status", "text"),
        ("created_at", "created_at", "text"),
    ],
    "review_items": [
        ("review_item_id", "review_item_id", "text"),
        ("submission_id", "submission_id", "text"),
        ("review_type", "review_type", "text"),
        ("status", "status", "text"),
        ("trigger_code", "trigger_code", "text"),
        ("detected_stage", "detected_stage", "text"),
        ("reason", "reason", "text"),
        ("evidence_refs_json", "evidence_refs", "json"),
        ("affected_page_refs_json", "affected_page_refs", "json"),
        ("attempted_actions_json", "attempted_actions", "json"),
        ("allowed_decisions_json", "allowed_decisions", "json"),
        ("recommendation_json", "recommendation", "json"),
        ("suggested_next_task_json", "suggested_next_task", "json"),
        ("version", "version", "int"),
        ("rule_gap_flag", "rule_gap_flag", "bool"),
        ("created_at", "created_at", "text"),
        ("resolved_at", "resolved_at", "text"),
    ],
    "review_decisions": [
        ("review_decision_id", "review_decision_id", "text"),
        ("review_item_id", "review_item_id", "text"),
        ("decision_code", "decision_code", "text"),
        ("review_item_version", "review_item_version", "int"),
        ("user_note", "user_note", "text"),
        ("target_refs_json", "target_refs", "json"),
        ("explicit_confirmation", "explicit_confirmation", "bool"),
        ("execution_status", "execution_status", "text"),
        ("result_json", "result", "json"),
        ("created_at", "created_at", "text"),
        ("executed_at", "executed_at", "text"),
    ],
    "rule_candidates": [
        ("candidate_id", "candidate_id", "text"),
        ("title", "title", "text"),
        ("gap_summary", "gap_summary", "text"),
        ("trigger_pattern_json", "trigger_pattern", "json"),
        ("affected_steps_json", "affected_steps", "json"),
        ("status", "status", "text"),
        ("created_at", "created_at", "text"),
        ("updated_at", "updated_at", "text"),
    ],
    "rule_candidate_cases": [
        ("candidate_id", "candidate_id", "text"),
        ("review_item_id", "review_item_id", "text"),
        ("case_summary", "case_summary", "text"),
        ("created_at", "created_at", "text"),
    ],
}

# 多维表格逻辑表名（契约字段名，向用户展示的名称可以本地化）
TABLES = {name: name for name in TABLE_SCHEMAS}

ENTITY_CLASSES: dict[str, type] = {
    "submissions": Submission,
    "original_evidence": OriginalEvidence,
    "evidence_index": DerivedEvidence,
    "knowledge_pages": KnowledgePageRecord,
    "evidence_contributions": EvidenceContributionRecord,
    "processing_records": ProcessingRecord,
    "understanding_results": UnderstandingResultRecord,
    "review_items": ReviewItemRecord,
    "review_decisions": ReviewDecisionRecord,
    "rule_candidates": RuleCandidateRecord,
    "rule_candidate_cases": RuleCandidateCaseRecord,
}


def map_cli_error(error: dict, what: str) -> CapsuleError:
    """把 lark-cli 错误信封的 error 负载映射为领域错误。"""
    code = error.get("code", 0) or 0
    message = error.get("msg") or error.get("message") or ""
    if code == 1254291 or code == 429:
        return CapsuleError(RATE_LIMITED, f"{what} 触发限流", retryable=True)
    if code in (1254300, 1254301):
        return CapsuleError(NOT_FOUND, f"{what}：资源不存在")
    if code == 1254290:
        return CapsuleError(STALE_VERSION, f"{what}：版本冲突")
    return CapsuleError(
        DEPENDENCY_UNAVAILABLE, f"{what}：飞书返回错误 {code} {message}".strip())


class LarkCliRunner:
    """执行 lark-cli 子进程；测试注入 fake runner。

    统一处理 1.0.94 的两种输出：
    - 信封 {"ok":true,...} / {"ok":false,"error":{...}}（错误伴随非零退出码）
    - 原始 JSON（whoami 等无信封命令）
    """

    def __init__(self, cli_path: str):
        self.cli_path = cli_path

    def run(self, args: list[str], timeout: int = 60,
            stdin_data: str | None = None) -> dict:
        completed = subprocess.run(
            [self.cli_path, *args], capture_output=True, text=True,
            timeout=timeout, input=stdin_data)
        stdout = (completed.stdout or "").strip()
        parsed: Any = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = None
        what = f"lark-cli {' '.join(args[:2])}"
        if isinstance(parsed, dict) and "ok" in parsed:
            if parsed.get("ok") is True:
                return parsed
            raise map_cli_error(parsed.get("error") or {}, what)
        if completed.returncode != 0:
            detail = (completed.stderr or stdout)[:500]
            raise CapsuleError(DEPENDENCY_UNAVAILABLE, f"{what} 调用失败：{detail}")
        if not isinstance(parsed, dict):
            raise CapsuleError(
                DEPENDENCY_UNAVAILABLE, f"{what} 输出不是合法 JSON：{stdout[:200]}")
        return parsed


class FeishuBackend:
    """经 lark-cli 的飞书终态存储实现。"""

    def __init__(self, runner: LarkCliRunner | Callable[[list[str]], dict],
                 wiki_space_id: str, base_app_token: str,
                 drive_root_folder_id: str, user_id: str = ""):
        self.runner = runner if callable(runner) else runner.run
        self.wiki_space_id = wiki_space_id
        self.base_app_token = base_app_token
        self.drive_root_folder_id = drive_root_folder_id
        self.user_id = user_id
        self._branch_tokens: dict[str, str] = {}
        # 进程内写入缓存：表 → 实体 ID → 记录（Base 字段 + record_id）。
        # 飞书 Base 过滤查询对刚写入的记录存在秒级索引延迟（最终一致），
        # read-after-write（同一进程内写后立即回读）改走本缓存。
        self._record_cache: dict[str, dict[str, dict]] = {}

    # ---- 基础 ----
    def _run(self, *args: str, timeout: int = 60,
             stdin_data: str | None = None) -> dict:
        return self.runner(list(args), timeout=timeout, stdin_data=stdin_data)

    def _check_response(self, result: dict, what: str) -> dict:
        """解开 lark-cli 信封；兼容 fake runner 的 {"code":N} 旧格式。"""
        if not isinstance(result, dict):
            raise CapsuleError(DEPENDENCY_UNAVAILABLE, f"{what}：lark-cli 返回格式异常")
        if "ok" in result:
            if result.get("ok") is True:
                data = result.get("data")
                return data if isinstance(data, dict) else {}
            raise map_cli_error(result.get("error") or {}, what)
        code = result.get("code", 0) or 0
        if code == 0:
            data = result.get("data")
            return data if isinstance(data, dict) else result
        raise map_cli_error({"code": code}, what)

    # ---- 检查与初始化 ----
    def check(self) -> dict:
        try:
            auth = self._check_response(
                self._run("auth", "status", "--verify"), "身份检查")
            auth_status = auth.get("status")
        except CapsuleError:
            # 部分运行环境的凭证由外部注入，lark-cli 不支持交互式 auth
            # 管理；回退到 whoami 验证当前身份与令牌可用性。
            who = self._check_response(self._run("whoami"), "身份检查")
            auth_status = who.get("tokenStatus")
            if auth_status != "ready":
                raise CapsuleError(
                    DEPENDENCY_UNAVAILABLE, "lark-cli 令牌不可用", details=who)
            if who.get("identity") != "user":
                raise CapsuleError(
                    DEPENDENCY_UNAVAILABLE,
                    "飞书后端需要用户身份（wiki/云空间为用户资源），"
                    f"当前身份：{who.get('identity')}",
                    details=who)
        ready = {
            "backend": "feishu",
            "ok": bool(self.wiki_space_id and self.base_app_token
                       and self.drive_root_folder_id),
            "auth": auth_status,
            "wiki_space_id": self.wiki_space_id,
            "base_app_token": self.base_app_token,
            "drive_root_folder_id": self.drive_root_folder_id,
        }
        if not ready["ok"]:
            raise CapsuleError(
                POLICY_BLOCKED, "飞书资源标识未配置（知识库/多维表格/云盘目录）",
                details=ready)
        return ready

    def _list_tables(self) -> list[dict]:
        data = self._check_response(
            self._run("base", "+table-list", "--base-token", self.base_app_token),
            "列出现有数据表")
        return data.get("tables", [])

    def bootstrap(self, environment: str) -> dict:
        if environment != "acceptance":
            raise CapsuleError(
                POLICY_BLOCKED, "bootstrap-test-space 只允许测试环境，"
                "并要求明确的测试环境标识")
        # 重建数据表会使全部 record_id 失效，写入缓存作废
        self._record_cache.clear()
        created = {"branches": [], "tables": []}
        # 1) 知识库分支：已存在则复用，缺失则创建（幂等）
        existing = self._list_root_nodes()
        existing_titles = {n.get("title") for n in existing}
        for branch in WIKI_BRANCHES.values():
            if branch in existing_titles:
                node = next(n for n in existing if n.get("title") == branch)
            else:
                node = self._check_response(
                    self._run("wiki", "+node-create",
                              "--space-id", self.wiki_space_id,
                              "--title", branch, "--obj-type", "docx"),
                    f"创建分支 {branch}")
            created["branches"].append(node.get("node_token", branch))
        # 2) 数据表：先创建无同名冲突的新表，再"删除旧表+重建"同名的
        #    （Base 必须保留至少一张表，不能先删唯一的旧表）
        existing_names = {t.get("name") for t in self._list_tables()}
        for table in TABLES.values():
            if table not in existing_names:
                self._create_table(table)
        for table in TABLES.values():
            if table in existing_names:
                self._check_response(
                    self._run("base", "+table-delete",
                              "--base-token", self.base_app_token,
                              "--table-id", table, "--yes"),
                    f"删除旧数据表 {table}")
                self._create_table(table)
            created["tables"].append(table)
        created["attachment_fields"] = 0
        return created

    def _create_table(self, table: str) -> None:
        fields = [{"name": base_name, "type": "text"}
                  for base_name, _, _ in TABLE_SCHEMAS[table]]
        self._check_response(
            self._run("base", "+table-create",
                      "--base-token", self.base_app_token,
                      "--name", table,
                      "--fields", json.dumps(fields, ensure_ascii=False)),
            f"创建数据表 {table}")

    def _list_root_nodes(self) -> list[dict]:
        data = self._check_response(
            self._run("wiki", "+node-list", "--space-id", self.wiki_space_id),
            "读取知识库根节点")
        return data.get("nodes", [])

    def _branch_node_token(self, branch_title: str) -> str:
        """查找分支节点 token（进程内缓存）。"""
        if branch_title in self._branch_tokens:
            return self._branch_tokens[branch_title]
        for node in self._list_root_nodes():
            if node.get("title") == branch_title:
                token = node.get("node_token", "")
                self._branch_tokens[branch_title] = token
                return token
        raise CapsuleError(
            NOT_FOUND, f"知识库分支不存在：{branch_title}（请先 bootstrap）")

    # ---- 多维表格原语：实体 ID 存普通字段，经 +record-list 定位 record_id ----
    def _query_page(self, table: str, criteria: dict[str, Any],
                    limit: int, offset: int) -> tuple[list[dict], bool]:
        """单页查询，返回 (记录列表, has_more)；每条记录含 record_id 与字段。"""
        args = ["base", "+record-list", "--base-token", self.base_app_token,
                "--table-id", table, "--limit", str(max(1, min(limit, 200))),
                "--offset", str(offset), "--format", "json"]
        if criteria:
            conditions = [[field, "==", value] for field, value in criteria.items()]
            args += ["--filter-json", json.dumps(
                {"logic": "and", "conditions": conditions}, ensure_ascii=False)]
        data = self._check_response(self._run(*args), f"查询 {table}")
        columns: list[str] = data.get("fields", [])
        rows: list = data.get("data", []) or []
        record_ids: list = data.get("record_id_list", []) or []
        records = []
        for i, row in enumerate(rows):
            fields = {columns[j]: row[j] for j in range(min(len(columns), len(row)))}
            fields["record_id"] = record_ids[i] if i < len(record_ids) else ""
            records.append(fields)
        return records, bool(data.get("has_more"))

    @staticmethod
    def _matches(record: dict, criteria: dict[str, Any]) -> bool:
        return all(record.get(field) == value
                   for field, value in criteria.items())

    @staticmethod
    def _next_revision(revision_id: str) -> str:
        """页面版本号递增（rev-1 → rev-2，与内存后端语义一致）。"""
        try:
            return f"rev-{int(revision_id.rsplit('-', 1)[1]) + 1}"
        except (ValueError, IndexError):
            return f"{revision_id}-2"

    @staticmethod
    def _strip_doc_title(content: str, title: str) -> str:
        """剥离飞书 markdown 导出附带的文档标题块。

        飞书把文档标题导出为正文前的标题块，形式取决于正文结构：
        正文无一级标题时用「# 标题」，正文含一级标题（且不在开头）
        时改用「<title>标题</title>」避免歧义；正文以一级标题开头时
        不附加标题块。标题块是文档属性而非写入的正文内容。"""
        for prefix in (f"# {title}\n\n", f"<title>{title}</title>\n\n"):
            if title and content.startswith(prefix):
                return content[len(prefix):]
        return content

    def _read_doc_body(self, docx_token: str, title: str,
                       content_hash: str, *,
                       decode: Callable[[str], str] | None = None) -> str:
        """读回云文档正文，以记录的内容哈希仲裁导出形式。

        飞书 markdown 导出的标题块形式取决于正文结构：正文无一级
        标题时为「# 标题」，正文含一级标题时为「<title>标题</title>」。
        写入时正文的首个一级标题还会被提升为文档标题并从正文移除，
        因此除原样与剥离标题块外，还需尝试把标题行拼回正文开头。
        都不匹配时返回原样，让上层写后核对发现手工修改（PARTIAL_WRITE）。
        decode 非空时（证据文字）对候选先反转义再比对与返回，
        与写入侧转义互逆，保证字面特殊字符逐字节往返。"""
        content, _ = self._fetch_doc(docx_token)
        tag_prefix = f"<title>{title}</title>\n\n"
        candidates = [content, self._strip_doc_title(content, title)]
        if content.startswith(tag_prefix):
            # 首个一级标题被提升为文档标题：拼回正文开头还原写入内容
            candidates.append(f"# {title}\n\n" + content[len(tag_prefix):])
        if content_hash:
            for candidate in candidates:
                text = decode(candidate) if decode else candidate
                if fp.content_hash(text) == content_hash:
                    return text
        fallback = candidates[0]
        return decode(fallback) if decode else fallback

    def _query(self, table: str, criteria: dict[str, Any],
               limit: int = 200, *, include_cached: bool = True) -> list[dict]:
        """查询并处理全部分页（每页最多 200 条，契约要求）。

        本进程刚写入的记录优先并入结果（飞书过滤查询对刚写入记录
        有秒级索引延迟），与远端结果按 record_id 去重合并。
        include_cached=False 时绕过缓存，强制从飞书远端读取。
        """
        records: list[dict] = []
        seen: set[str] = set()
        if include_cached:
            for cached in self._record_cache.get(table, {}).values():
                if cached.get("record_id") and self._matches(cached, criteria):
                    records.append(dict(cached))
                    seen.add(cached["record_id"])
        offset = 0
        while len(records) < limit:
            page, has_more = self._query_page(
                table, criteria, limit - len(records), offset)
            for record in page:
                if record.get("record_id") and record["record_id"] not in seen:
                    records.append(record)
                    seen.add(record["record_id"])
                    self._record_cache.setdefault(table, {})[
                        record[TABLE_SCHEMAS[table][0][0]]] = dict(record)
            if not has_more or not page:
                break
            offset += len(page)
        return records[:limit]

    def _find_one(self, table: str, entity_id: str, *,
                  fresh: bool = False) -> dict | None:
        """定位单条记录；fresh=True 绕过缓存强制从飞书读取。"""
        if not fresh:
            cached = self._record_cache.get(table, {}).get(entity_id)
            if cached is not None:
                return dict(cached)
        id_field = TABLE_SCHEMAS[table][0][0]
        records = self._query(
            table, {id_field: entity_id}, limit=2, include_cached=not fresh)
        if records:
            record = records[0]
            self._record_cache.setdefault(table, {})[entity_id] = dict(record)
            return record
        return None

    def _to_base_fields(self, table: str, entity: Any) -> dict:
        fields: dict[str, Any] = {}
        for base_name, entity_name, kind in TABLE_SCHEMAS[table]:
            value = getattr(entity, entity_name)
            if kind == "text":
                fields[base_name] = "" if value is None else str(value)
            elif kind == "json":
                fields[base_name] = json.dumps(
                    value, ensure_ascii=False) if value else ""
            elif kind == "bool":
                fields[base_name] = "true" if value else "false"
            elif kind == "int":
                fields[base_name] = str(value) if value is not None else ""
        return fields

    def _from_base_fields(self, table: str, raw: dict) -> Any:
        """Base 字段 → 实体；空值跳过让 pydantic 用模型默认值。"""
        data: dict[str, Any] = {}
        for base_name, entity_name, kind in TABLE_SCHEMAS[table]:
            value = raw.get(base_name)
            if kind == "text":
                if value is not None:
                    data[entity_name] = value if isinstance(value, str) else str(value)
            elif kind == "json":
                if isinstance(value, (list, dict)):
                    data[entity_name] = value
                elif isinstance(value, str) and value.strip():
                    try:
                        data[entity_name] = json.loads(value)
                    except json.JSONDecodeError:
                        pass  # 保留模型默认值
            elif kind == "bool":
                data[entity_name] = value == "true" or value is True
            elif kind == "int":
                if value not in (None, ""):
                    try:
                        data[entity_name] = int(value)
                    except (TypeError, ValueError):
                        pass
        return ENTITY_CLASSES[table](**data)

    def _create_record(self, table: str, entity: Any) -> None:
        fields = self._to_base_fields(table, entity)
        entity_id = fields[TABLE_SCHEMAS[table][0][0]]
        payload = {"create_records": [fields]}
        data = self._check_response(
            self._run("base", "+record-batch-create",
                      "--base-token", self.base_app_token,
                      "--table-id", table,
                      "--json", json.dumps(payload, ensure_ascii=False)),
            f"写入 {table}")
        # 创建响应返回 record_id_list；登记写入缓存，使同一进程内
        # 随后的回读（read-after-write）不依赖过滤查询索引
        record_ids = data.get("record_id_list") or []
        if record_ids and isinstance(record_ids[0], str) and record_ids[0]:
            self._record_cache.setdefault(table, {})[entity_id] = {
                "record_id": record_ids[0], **fields}

    def _update_fields(self, table: str, entity_id: str, fields: dict) -> None:
        record = self._find_one(table, entity_id)
        if record is None:
            raise CapsuleError(NOT_FOUND, f"{table} 记录不存在：{entity_id}")
        payload = {"update_records": {record["record_id"]: fields}}
        self._check_response(
            self._run("base", "+record-batch-update",
                      "--base-token", self.base_app_token,
                      "--table-id", table,
                      "--json", json.dumps(payload, ensure_ascii=False)),
            f"更新 {table}/{entity_id}")
        # 同步合并写入缓存，保持本进程内回读一致
        cached = self._record_cache.get(table, {}).get(entity_id)
        if cached is not None:
            cached.update(fields)

    def _update_entity(self, table: str, entity: Any) -> None:
        entity_id = getattr(entity, TABLE_SCHEMAS[table][0][1])
        self._update_fields(table, entity_id, self._to_base_fields(table, entity))

    def _delete_record(self, table: str, entity_id: str) -> None:
        record = self._find_one(table, entity_id)
        if record is None:
            raise CapsuleError(NOT_FOUND, f"{table} 记录不存在：{entity_id}")
        self._check_response(
            self._run("base", "+record-delete",
                      "--base-token", self.base_app_token,
                      "--table-id", table,
                      "--record-id", record["record_id"], "--yes"),
            f"删除 {table}/{entity_id}")
        self._record_cache.get(table, {}).pop(entity_id, None)

    # ---- 云文档原语 ----
    def _create_wiki_doc(self, title: str, branch_title: str) -> tuple[str, str, str]:
        """在知识库分支下创建云文档，返回 (docx_token, node_token, revision_id)。"""
        parent = self._branch_node_token(branch_title)
        data = self._check_response(
            # --title= 连写：标题以 - 开头时不被 CLI 误认为 flag
            self._run("docs", "+create", f"--title={title}",
                      "--doc-format", "markdown", "--parent-token", parent),
            f"创建云文档「{title}」")
        doc = data.get("document", {})
        docx_token = doc.get("document_id", "")
        node_token = self._find_wiki_node_for_doc(parent, docx_token)
        return docx_token, node_token, str(doc.get("revision_id", ""))

    def _find_wiki_node_for_doc(self, parent_token: str, docx_token: str) -> str:
        data = self._check_response(
            self._run("wiki", "+node-list", "--space-id", self.wiki_space_id,
                      "--parent-node-token", parent_token),
            "查找文档知识库节点")
        for node in data.get("nodes", []):
            if node.get("obj_token") == docx_token:
                return node.get("node_token", "")
        return ""

    def _fetch_doc(self, docx_token: str) -> tuple[str, str]:
        """读取云文档，返回 (markdown 内容, revision_id)。"""
        data = self._check_response(
            self._run("docs", "+fetch", "--doc", docx_token,
                      "--doc-format", "markdown"),
            f"读取文档 {docx_token}")
        doc = data.get("document", {})
        return doc.get("content", ""), str(doc.get("revision_id", ""))

    def _overwrite_doc(self, docx_token: str, revision_id: str,
                       content: str) -> str:
        """以指定基准版本覆盖文档，返回新 revision_id（乐观并发）。

        正文经 stdin 传递（--content -，lark-cli 官方推荐方式）：
        彻底绕开命令行参数解析（以 - 开头的正文会被误认为 flag）
        与 argv 长度限制。"""
        args = ["docs", "+update", "--doc", docx_token,
                "--command", "overwrite", "--content", "-",
                "--doc-format", "markdown"]
        if revision_id:
            args += ["--revision-id", revision_id]
        data = self._check_response(
            self._run(*args, stdin_data=content), f"更新文档 {docx_token}")
        doc = data.get("document", {})
        return str(doc.get("revision_id", ""))

    # ---- 提交与入口去重 ----
    def find_submission_by_fingerprint(self, fingerprint: str) -> Submission | None:
        records = self._query("submissions",
                              {"content_fingerprint": fingerprint}, limit=1)
        return self._from_base_fields("submissions", records[0]) if records else None

    def save_submission(self, submission: Submission) -> None:
        self._create_record("submissions", submission)

    def get_submission(self, submission_id: str) -> Submission | None:
        record = self._find_one("submissions", submission_id)
        return self._from_base_fields("submissions", record) if record else None

    # ---- 原始证据（仅 URL） ----
    def save_original_evidence(self, original: OriginalEvidence) -> None:
        self._create_record("original_evidence", original)

    def get_original_evidence(self, original_evidence_id: str) -> OriginalEvidence | None:
        record = self._find_one("original_evidence", original_evidence_id)
        return self._from_base_fields("original_evidence", record) if record else None

    def get_original_evidence_by_submission(self, submission_id: str) -> OriginalEvidence | None:
        records = self._query("original_evidence",
                              {"submission_id": submission_id}, limit=1)
        return self._from_base_fields("original_evidence", records[0]) if records else None

    def delete_original_evidence(self, original_evidence_id: str) -> None:
        self._delete_record("original_evidence", original_evidence_id)

    # ---- 衍生证据 ----
    def save_derived_evidence(self, evidence: DerivedEvidence) -> None:
        docx_token, node_token, _ = self._create_wiki_doc(
            evidence.title, WIKI_BRANCHES["evidence"])
        payload = evidence.model_copy(update={
            "docx_token": docx_token, "wiki_node_token": node_token})
        self._create_record("evidence_index", payload)

    def get_derived_evidence(self, evidence_id: str) -> DerivedEvidence | None:
        record = self._find_one("evidence_index", evidence_id)
        return self._from_base_fields("evidence_index", record) if record else None

    def list_evidence_by_submission(self, submission_id: str) -> list[DerivedEvidence]:
        records = self._query("evidence_index",
                              {"submission_id": submission_id})
        return [self._from_base_fields("evidence_index", r) for r in records]

    def save_evidence_content(self, evidence_id: str, content: str) -> None:
        evidence = self.get_derived_evidence(evidence_id)
        if not evidence:
            raise CapsuleError(NOT_FOUND, f"证据不存在：{evidence_id}")
        _, revision = self._fetch_doc(evidence.docx_token or "")
        # 证据文字按字面存储：转义 markdown 特殊字符，读回反转义还原
        self._overwrite_doc(evidence.docx_token or "", revision,
                            escape_markdown(content))

    def read_evidence_content(self, evidence_id: str) -> str:
        evidence = self.get_derived_evidence(evidence_id)
        if not evidence:
            raise CapsuleError(NOT_FOUND, f"证据不存在：{evidence_id}")
        return self._read_doc_body(evidence.docx_token or "",
                                   evidence.title, evidence.content_hash,
                                   decode=decode_evidence_markdown)

    def mark_evidence_deleted(self, evidence_id: str) -> None:
        evidence = self.get_derived_evidence(evidence_id)
        if not evidence:
            raise CapsuleError(NOT_FOUND, f"证据不存在：{evidence_id}")
        # 文字资源删除需要明确确认：调用方（review decide）已确认
        self._check_response(
            self._run("drive", "+delete", "--file-token", evidence.docx_token or "",
                      "--type", "docx", "--yes"),
            f"删除证据云文档 {evidence_id}")
        self._update_fields("evidence_index", evidence_id, {"status": "deleted"})

    # ---- 知识页 ----
    def save_page(self, page: KnowledgePageRecord) -> None:
        docx_token, node_token, _ = self._create_wiki_doc(
            page.title, WIKI_BRANCHES[page.page_type])
        # Base 记录保留引擎传入的页面版本号（rev-N 体系）；
        # 飞书云文档自身的 revision 是另一套版本体系，仅供
        # 写入时乐观并发使用（写入前先 fetch），不写入记录
        payload = page.model_copy(update={
            "docx_token": docx_token, "wiki_node_token": node_token})
        self._create_record("knowledge_pages", payload)

    def get_page(self, page_id: str) -> KnowledgePageRecord | None:
        record = self._find_one("knowledge_pages", page_id)
        return self._from_base_fields("knowledge_pages", record) if record else None

    def list_pages(self, page_type=None, title_contains=None, status=None,
                   limit=50, offset=0) -> list[KnowledgePageRecord]:
        criteria: dict[str, Any] = {}
        if page_type:
            criteria["page_type"] = page_type
        if status:
            criteria["status"] = status
        records = self._query("knowledge_pages", criteria, limit=limit + max(offset, 0))
        records = records[offset:]
        pages = [self._from_base_fields("knowledge_pages", r) for r in records]
        if title_contains:
            pages = [p for p in pages if title_contains in p.title]
        return pages

    def read_page_content(self, page_id: str) -> str:
        page = self.get_page(page_id)
        if not page:
            raise CapsuleError(NOT_FOUND, f"知识页不存在：{page_id}")
        return self._read_doc_body(page.docx_token or "",
                                   page.title, page.content_hash)

    def update_page_content(self, page_id: str, revision_id: str, content: str,
                            content_hash: str) -> KnowledgePageRecord:
        page = self.get_page(page_id)
        if not page:
            raise CapsuleError(NOT_FOUND, f"知识页不存在：{page_id}")
        if page.revision_id != revision_id:
            raise CapsuleError(
                STALE_VERSION,
                f"页面「{page.title}」版本已变化（读取 {revision_id}，"
                f"当前 {page.revision_id}）")
        # 云文档写入使用读取时取得的版本（乐观并发）：写入前先读
        # 文档当前 revision（飞书体系，与页面的 rev-N 版本号无关）
        _, doc_revision = self._fetch_doc(page.docx_token or "")
        self._overwrite_doc(page.docx_token or "", doc_revision, content)
        new_revision = self._next_revision(revision_id)
        self._update_fields("knowledge_pages", page_id,
                            {"revision_id": new_revision,
                             "content_hash": content_hash})
        # 写后核对：强制从飞书重读（绕过本进程写入缓存）；
        # 过滤索引延迟读不到时，回退本进程写入值（更新调用已确认成功）
        record = (self._find_one("knowledge_pages", page_id, fresh=True)
                  or self._find_one("knowledge_pages", page_id))
        updated = (self._from_base_fields("knowledge_pages", record)
                   if record else None)
        # 写后核对：重新读取哈希，防止手工修改或写入不一致
        if updated and updated.content_hash != content_hash:
            raise CapsuleError(
                PARTIAL_WRITE, f"页面「{page.title}」写后核对不一致")
        return updated  # type: ignore[return-value]

    def set_page_status(self, page_id: str, status: str) -> None:
        self._update_fields("knowledge_pages", page_id, {"status": status})

    # ---- 追溯关系 ----
    def save_contribution(self, contribution: EvidenceContributionRecord) -> None:
        self._create_record("evidence_contributions", contribution)

    def list_contributions(self, page_id=None, evidence_id=None) -> list[EvidenceContributionRecord]:
        criteria: dict[str, Any] = {}
        if page_id:
            criteria["page_id"] = page_id
        if evidence_id:
            criteria["evidence_id"] = evidence_id
        records = self._query("evidence_contributions", criteria)
        return [self._from_base_fields("evidence_contributions", r) for r in records]

    # ---- 处理记录与理解结果 ----
    def save_processing_record(self, record: ProcessingRecord) -> None:
        self._create_record("processing_records", record)

    def list_processing_records(self, submission_id: str) -> list[ProcessingRecord]:
        records = self._query("processing_records",
                              {"submission_id": submission_id})
        return [self._from_base_fields("processing_records", r) for r in records]

    def save_understanding(self, understanding: UnderstandingResultRecord) -> None:
        self._create_record("understanding_results", understanding)

    def list_understandings(self, submission_id: str) -> list[UnderstandingResultRecord]:
        records = self._query("understanding_results",
                              {"submission_id": submission_id})
        return [self._from_base_fields("understanding_results", r) for r in records]

    # ---- 审核 ----
    def save_review_item(self, item: ReviewItemRecord) -> None:
        self._create_record("review_items", item)

    def get_review_item(self, review_item_id: str) -> ReviewItemRecord | None:
        record = self._find_one("review_items", review_item_id)
        return self._from_base_fields("review_items", record) if record else None

    def list_review_items(self, submission_id=None, status=None, review_type=None,
                          trigger_code=None, detected_stage=None, rule_gap_flag=None,
                          limit=50, offset=0) -> list[ReviewItemRecord]:
        criteria: dict[str, Any] = {}
        if submission_id:
            criteria["submission_id"] = submission_id
        if status:
            criteria["status"] = status
        if review_type:
            criteria["review_type"] = review_type
        if trigger_code:
            criteria["trigger_code"] = trigger_code
        if detected_stage:
            criteria["detected_stage"] = detected_stage
        if rule_gap_flag is not None:
            criteria["rule_gap_flag"] = "true" if rule_gap_flag else "false"
        records = self._query("review_items", criteria,
                              limit=limit + max(offset, 0))
        records = records[offset:]
        return [self._from_base_fields("review_items", r) for r in records]

    def update_review_item(self, item: ReviewItemRecord) -> None:
        self._update_entity("review_items", item)

    def append_review_decision(self, decision: ReviewDecisionRecord) -> None:
        self._create_record("review_decisions", decision)

    def list_review_decisions(self, review_item_id: str) -> list[ReviewDecisionRecord]:
        records = self._query("review_decisions",
                              {"review_item_id": review_item_id})
        return [self._from_base_fields("review_decisions", r) for r in records]

    # ---- 规则改进候选 ----
    def save_rule_candidate(self, candidate: RuleCandidateRecord) -> None:
        self._create_record("rule_candidates", candidate)

    def get_rule_candidate(self, candidate_id: str) -> RuleCandidateRecord | None:
        record = self._find_one("rule_candidates", candidate_id)
        return self._from_base_fields("rule_candidates", record) if record else None

    def list_rule_candidates(self, status=None, limit=50, offset=0) -> list[RuleCandidateRecord]:
        criteria = {"status": status} if status else {}
        records = self._query("rule_candidates", criteria,
                              limit=limit + max(offset, 0))
        records = records[offset:]
        return [self._from_base_fields("rule_candidates", r) for r in records]

    def update_rule_candidate(self, candidate: RuleCandidateRecord) -> None:
        self._update_entity("rule_candidates", candidate)

    def save_rule_candidate_case(self, case: RuleCandidateCaseRecord) -> None:
        self._create_record("rule_candidate_cases", case)

    def list_rule_candidate_cases(self, candidate_id: str) -> list[RuleCandidateCaseRecord]:
        records = self._query("rule_candidate_cases",
                              {"candidate_id": candidate_id})
        return [self._from_base_fields("rule_candidate_cases", r) for r in records]
