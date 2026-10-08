"""Regression tests for issue #1 - path traversal via unvalidated
investigation_id / resource_id used as raw filenames.

Covers:
  * utils/path_safety.validate_file_id - the allowlist itself
  * the two store sinks (conversation_store, log_investigation_store)
  * the trust-boundary split in both managers
  * the pipeline writers (report_writer, prompt_builder)

These tests use tmp_path fixtures and never touch the live output/ tree
or AWS - the stores' directory constants are monkeypatched per-test.
"""

import json

import pytest

from utils.path_safety import validate_file_id

# ---------------------------------------------------------------------------
# What a malicious ID that must NEVER reach the filesystem looks like.
# Every entry previously produced a path outside the intended directory.
# ---------------------------------------------------------------------------
TRAVERSAL_PAYLOADS = [
    "../../etc/cron.d/x",
    "..\\..\\windows\\system32\\x",
    "../../../../tmp/evil",
    "run__../../etc/passwd",
    "%2e%2e%2f..%2fetc",  # %-encoding is not decoded here, but % must not pass the allowlist anyway
    "/etc/passwd",
    "a/../../b",
    "..",
    "foo bar",            # spaces are not part of any legitimate ID
    "foo;rm -rf",         # shell metacharacters
    "foo\nevil",          # newline injection
    "x" * 600,            # oversized (exceeds MAX_ID_LENGTH)
    "",                   # empty
    ".hidden_leading_dot",
]

LEGITIMATE_IDS = [
    "i-0abc123def456789",
    "app/my-alb/80abcdef12345678",
    "targetgroup/my-tg/1234abcd",
    "my-auto-scaling-group",
    "run_20261008T154000",
    "run_20261008T154000__i-0abc123def456789",
    "run_20261008T154000__app/my-alb/80abcdef12345678",
]


class TestValidateFileId:
    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_rejects_malicious_or_invalid_ids(self, payload):
        with pytest.raises(ValueError):
            validate_file_id(payload)

    @pytest.mark.parametrize("good", LEGITIMATE_IDS)
    def test_accepts_legitimate_aws_and_composite_ids(self, good):
        assert validate_file_id(good) == good


class TestConversationStore:
    @pytest.fixture(autouse=True)
    def store_dir(self, tmp_path, monkeypatch):
        from utils import conversation_store
        monkeypatch.setattr(conversation_store, "CONVERSATIONS_DIR", tmp_path / "conversations")
        return conversation_store, tmp_path

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_append_turn_cannot_escape(self, store_dir, payload):
        conversation_store, tmp_path = store_dir
        with pytest.raises(ValueError):
            conversation_store.append_turn(payload, {"role": "user"}, {"role": "assistant"})
        # Nothing created in/outside this test's tmp dirs.
        assert [p for p in tmp_path.glob("**/*.json")] == []

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_load_session_rejects_or_returns_none(self, store_dir, payload):
        conversation_store, tmp_path = store_dir
        assert conversation_store.load_session(payload) is None

    def test_legitimate_round_trip(self, store_dir):
        conversation_store, _ = store_dir
        session = conversation_store.get_or_create_session(
            investigation_id="run_1__i-0abc123",
            run_id="run_1",
            resource_id="i-0abc123",
            resource_type="EC2",
            report_reference="output/reports/i-0abc123.json",
            context_reference="output/context/i-0abc123.json",
        )
        assert session["investigation_id"] == "run_1__i-0abc123"


class TestLogInvestigationStore:
    @pytest.fixture(autouse=True)
    def store_dir(self, tmp_path, monkeypatch):
        from utils import log_investigation_store
        monkeypatch.setattr(log_investigation_store, "LOG_INVESTIGATIONS_DIR", tmp_path / "log_investigations")
        return log_investigation_store, tmp_path

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_save_result_cannot_escape(self, store_dir, payload):
        log_investigation_store, tmp_path = store_dir
        with pytest.raises(ValueError):
            log_investigation_store.save_result(
                payload, "run_1", "i-0abc", None, "r.json", "c.json", {}, {},
            )
        assert [p for p in tmp_path.glob("**/*.json")] == []

    def test_legitimate_round_trip(self, store_dir):
        log_investigation_store, _ = store_dir
        result = log_investigation_store.save_result(
            "run_1__i-0abc", "run_1", "i-0abc", "EC2", "r.json", "c.json", {"a": 1}, {"b": 2},
        )
        assert result["investigation_id"] == "run_1__i-0abc"


class TestManagerSplit:
    """Both managers' _split_investigation_id must reject traversal at the
    trust boundary, mapping ValueError to their typed NotFound errors."""

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_follow_up_manager(self, payload):
        from api.follow_up_manager import _split_investigation_id, InvestigationNotFoundError
        with pytest.raises(InvestigationNotFoundError):
            _split_investigation_id(payload)

    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    def test_log_investigation_manager(self, payload):
        from api.log_investigation_manager import _split_investigation_id, LogInvestigationNotFoundError
        with pytest.raises(LogInvestigationNotFoundError):
            _split_investigation_id(payload)

    def test_follow_up_manager_valid(self):
        from api.follow_up_manager import _split_investigation_id
        run_id, resource_id = _split_investigation_id("run_1__i-0abc123")
        assert (run_id, resource_id) == ("run_1", "i-0abc123")

    def test_alb_resource_with_slashes_survives(self):
        from api.follow_up_manager import _split_investigation_id
        run_id, resource_id = _split_investigation_id("run_1__app/my-alb/80abcdef")
        assert resource_id == "app/my-alb/80abcdef"


class TestPipelineWriters:
    def test_report_writer_rejects_traversal(self, tmp_path, monkeypatch):
        from analyzer.report_writer import ReportWriter
        writer = ReportWriter()
        monkeypatch.setattr(writer, "output_dir", tmp_path / "reports")
        with pytest.raises(ValueError):
            writer.save("../../etc/cron.d/x", {"summary": "evil"})
        assert list(tmp_path.glob("**/*")) == []

    def test_report_writer_happy_path(self, tmp_path, monkeypatch):
        from analyzer.report_writer import ReportWriter
        writer = ReportWriter()
        (tmp_path / "reports").mkdir()
        monkeypatch.setattr(writer, "output_dir", tmp_path / "reports")
        out = writer.save("i-0abc123", {"summary": "ok"})
        assert json.loads(out.read_text())["summary"] == "ok"

    def test_prompt_builder_rejects_traversal(self, tmp_path):
        from llm.prompt_builder import PromptBuilder
        builder = PromptBuilder.__new__(PromptBuilder)
        builder.prompt_directory = tmp_path / "prompts"
        with pytest.raises(ValueError):
            builder._save_prompt("../../etc/cron.d/x", "prompt")
        assert list(tmp_path.glob("**/*")) == []
