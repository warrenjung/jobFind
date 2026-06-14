from datetime import datetime, timezone

import saved_answers as sa


class TestSafety:
    def test_rejects_sensitive_questions(self):
        assert sa.question_is_safe_to_save("What is your social security number?") is False
        assert sa.question_is_safe_to_save("Are you legally authorized to work?") is False
        assert sa.question_is_safe_to_save("Enter the verification code") is False

    def test_rejects_generic_placeholder_questions(self):
        assert sa.question_is_safe_to_save("Yes/No question on this step") is False
        assert sa.question_is_safe_to_save("Yes/No question on this step", "radio") is False
        assert sa.question_is_safe_to_save("Review this field on the page") is False

    def test_accepts_ordinary_questions(self):
        assert sa.question_is_safe_to_save("Why do you want this job?") is True


class TestNormalize:
    def test_normalize_question_is_lowercase_alnum(self):
        assert sa.normalize_question("Why do you want THIS job?!") == "why do you want this job"

    def test_clean_text_truncates(self):
        assert sa.clean_text("a" * 100, 10) == "a" * 10


class TestReadWrite:
    def test_read_missing_file_returns_empty(self, tmp_path):
        assert sa.read_saved_answers(tmp_path / "nope.json") == {"answers": []}

    def test_read_corrupt_file_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert sa.read_saved_answers(bad) == {"answers": []}

    def test_saved_answer_map_keys_on_normalized_question(self):
        payload = {"answers": [{"key": "why do you want this job", "answer": "Great team"}]}
        assert sa.saved_answer_map(payload) == {"why do you want this job": "Great team"}

    def test_saved_answer_map_skips_disabled_answers(self):
        payload = {
            "answers": [
                {"key": "why do you want this job", "answer": "Great team", "autofill_enabled": False},
                {"key": "ideal job", "answer": "A friendly team"},
            ]
        }
        assert sa.saved_answer_map(payload) == {"ideal job": "A friendly team"}


class TestUpsert:
    def _payload(self, answer="Because I love it."):
        return {
            "question": "Why do you want this job?",
            "answer": answer,
            "kind": "text",
            "job": {"title": "Barista", "company": "Cafe"},
        }

    def test_creates_then_reads_back(self, tmp_path):
        json_path = tmp_path / "saved.json"
        md_path = tmp_path / "saved.md"
        saved, error = sa.upsert_saved_answer(json_path, md_path, self._payload())
        assert error is None
        assert saved["times_saved"] == 1
        assert json_path.exists() and md_path.exists()
        assert "Why do you want this job" in md_path.read_text(encoding="utf-8")
        reloaded = sa.read_saved_answers(json_path)
        assert reloaded["answers"][0]["answer"] == "Because I love it."

    def test_updates_existing_question(self, tmp_path):
        json_path = tmp_path / "saved.json"
        md_path = tmp_path / "saved.md"
        sa.upsert_saved_answer(json_path, md_path, self._payload("first"))
        saved, error = sa.upsert_saved_answer(json_path, md_path, self._payload("second"))
        assert error is None
        assert saved["answer"] == "second"
        assert saved["times_saved"] == 2
        assert len(sa.read_saved_answers(json_path)["answers"]) == 1

    def test_upsert_preserves_disabled_autofill_setting(self, tmp_path):
        json_path = tmp_path / "saved.json"
        md_path = tmp_path / "saved.md"
        sa.upsert_saved_answer(json_path, md_path, self._payload("first"))
        sa.update_saved_answer(
            json_path,
            md_path,
            {"key": "why do you want this job", "autofill_enabled": False},
        )
        saved, error = sa.upsert_saved_answer(json_path, md_path, self._payload("second"))

        assert error is None
        assert saved["answer"] == "second"
        assert saved["autofill_enabled"] is False

    def test_rejects_sensitive_on_save(self, tmp_path):
        payload = {"question": "What is your SSN?", "answer": "123-45-6789"}
        saved, error = sa.upsert_saved_answer(tmp_path / "s.json", tmp_path / "s.md", payload)
        assert saved is None
        assert error

    def test_uses_injected_clock(self, tmp_path):
        fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
        saved, _ = sa.upsert_saved_answer(
            tmp_path / "s.json", tmp_path / "s.md", self._payload(), now=lambda: fixed
        )
        assert saved["updated_at"] == fixed.isoformat()

    def test_update_answer_text_and_markdown(self, tmp_path):
        json_path = tmp_path / "saved.json"
        md_path = tmp_path / "saved.md"
        sa.upsert_saved_answer(json_path, md_path, self._payload("first"))

        saved, error = sa.update_saved_answer(
            json_path,
            md_path,
            {
                "key": "why do you want this job",
                "answer": "Updated answer",
                "autofill_enabled": False,
            },
        )

        assert error is None
        assert saved["answer"] == "Updated answer"
        assert saved["autofill_enabled"] is False
        markdown = md_path.read_text(encoding="utf-8")
        assert "Updated answer" in markdown
        assert "| No |" in markdown
        assert sa.saved_answer_map(sa.read_saved_answers(json_path)) == {}

    def test_update_rejects_missing_key(self, tmp_path):
        saved, error = sa.update_saved_answer(tmp_path / "s.json", tmp_path / "s.md", {})

        assert saved is None
        assert "key is required" in error

    def test_delete_answer_removes_json_and_markdown(self, tmp_path):
        json_path = tmp_path / "saved.json"
        md_path = tmp_path / "saved.md"
        sa.upsert_saved_answer(json_path, md_path, self._payload())

        deleted, error = sa.delete_saved_answer(json_path, md_path, {"key": "why do you want this job"})

        assert error is None
        assert deleted is True
        assert sa.read_saved_answers(json_path) == {"answers": []}
        assert "Why do you want this job" not in md_path.read_text(encoding="utf-8")

    def test_delete_rejects_missing_key(self, tmp_path):
        deleted, error = sa.delete_saved_answer(tmp_path / "s.json", tmp_path / "s.md", {})

        assert deleted is False
        assert "key is required" in error
