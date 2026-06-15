"""Unit tests for the pure helpers in local_search_server.py."""

import io
import json

import autofill_browser as ab
import local_search_server as lss


class TestCleanFormValue:
    def test_returns_value(self):
        assert lss.clean_form_value({"location": ["San Jose, CA"]}, "location", "x") == "San Jose, CA"

    def test_falls_back_to_default(self):
        assert lss.clean_form_value({}, "location", "Cupertino, CA") == "Cupertino, CA"
        assert lss.clean_form_value({"location": ["  "]}, "location", "def") == "def"


class TestValidateForm:
    def test_valid(self):
        opts, error = lss.validate_form(
            {"location": ["San Jose, CA"], "radius": ["25"], "mode": ["full"], "min_score": ["60"]}
        )
        assert error is None
        assert opts["radius"] == "25" and opts["mode"] == "full" and opts["min_score"] == "60"

    def test_rejects_bad_radius(self):
        _, error = lss.validate_form({"radius": ["7"]})
        assert error is not None

    def test_rejects_bad_mode(self):
        _, error = lss.validate_form({"mode": ["turbo"]})
        assert error is not None

    def test_rejects_out_of_range_score(self):
        _, error = lss.validate_form({"min_score": ["150"]})
        assert error is not None

    def test_rejects_non_numeric_score(self):
        _, error = lss.validate_form({"min_score": ["abc"]})
        assert error is not None

    def test_accepts_personal_keywords(self):
        opts, error = lss.validate_form({"personal_keywords": [" barista, tutoring "]})

        assert error is None
        assert opts["personal_keywords"] == "barista, tutoring"

    def test_rejects_too_long_personal_keywords(self):
        _, error = lss.validate_form({"personal_keywords": ["x" * (lss.MAX_PERSONAL_KEYWORDS_LENGTH + 1)]})

        assert error == "Preferred job keywords are too long."


class TestBuildPipelineCommand:
    def _opts(self, mode="fast"):
        return {"location": "Cupertino, CA", "radius": "10", "mode": mode, "min_score": "50"}

    def test_never_requests_usajobs(self):
        for mode in ("fast", "full"):
            cmd = lss.build_pipeline_command(self._opts(mode))
            assert "--num-usajobs-results" not in cmd
            assert "--include-usajobs" not in cmd

    def test_fast_mode_uses_fewer_queries_and_one_page(self):
        cmd = lss.build_pipeline_command(self._opts("fast"))
        assert "--indeed-queries" in cmd
        i = cmd.index("--indeed-pages")
        assert cmd[i + 1] == "1"

    def test_full_mode_three_pages_no_query_override(self):
        cmd = lss.build_pipeline_command(self._opts("full"))
        assert "--indeed-queries" not in cmd
        i = cmd.index("--indeed-pages")
        assert cmd[i + 1] == "3"

    def test_passes_location_and_min_score(self):
        cmd = lss.build_pipeline_command(self._opts("fast"))
        assert "--location" in cmd and "Cupertino, CA" in cmd
        assert "--clean-min-score" in cmd and "50" in cmd

    def test_omits_blank_personal_keywords(self):
        cmd = lss.build_pipeline_command({**self._opts("fast"), "personal_keywords": ""})

        assert "--personal-keywords" not in cmd

    def test_passes_personal_keywords_when_present(self):
        cmd = lss.build_pipeline_command({**self._opts("fast"), "personal_keywords": "barista, tutoring"})

        i = cmd.index("--personal-keywords")
        assert cmd[i + 1] == "barista, tutoring"


class TestApplicationTracking:
    def test_upsert_application_writes_status_file(self, tmp_path, monkeypatch):
        status_file = tmp_path / "applications_status.json"
        monkeypatch.setattr(lss, "APPLICATIONS_FILE", status_file)

        application, error = lss.upsert_application(
            {
                "url": "https://www.indeed.com/viewjob?jk=abc",
                "title": "Cashier",
                "company": "Example Market",
                "source": "Indeed",
                "score": "95",
            },
            "Opened",
        )

        assert error is None
        assert application["status"] == "Opened"
        saved = json.loads(status_file.read_text())
        assert saved["applications"][0]["title"] == "Cashier"

    def test_upsert_application_rejects_bad_url(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lss, "APPLICATIONS_FILE", tmp_path / "applications_status.json")

        application, error = lss.upsert_application({"url": "not-a-url"}, "Opened")

        assert application is None
        assert error is not None

    def test_opening_job_does_not_downgrade_applied_status(self, tmp_path, monkeypatch):
        status_file = tmp_path / "applications_status.json"
        monkeypatch.setattr(lss, "APPLICATIONS_FILE", status_file)
        job = {
            "url": "https://www.indeed.com/viewjob?jk=abc",
            "title": "Cashier",
            "company": "Example Market",
        }

        applied, error = lss.upsert_application(job, "Applied")
        reopened, reopen_error = lss.upsert_application(job, "Opened")

        assert error is None
        assert reopen_error is None
        assert applied["status"] == "Applied"
        assert reopened["status"] == "Applied"
        saved = json.loads(status_file.read_text())
        assert saved["applications"][0]["status"] == "Applied"

    def test_application_queue_group_maps_statuses(self):
        assert lss.application_queue_group("") == "next"
        assert lss.application_queue_group("Opened") == "progress"
        assert lss.application_queue_group("Autofilled") == "progress"
        assert lss.application_queue_group("Needs follow-up") == "follow"
        assert lss.application_queue_group("Applied") == "done"
        assert lss.application_queue_group("Skipped") == "skipped"

    def test_group_application_records_sorts_into_queue_buckets(self):
        rows = [
            {"title": "New"},
            {"title": "Opened", "status": "Opened"},
            {"title": "Autofilled", "status": "Autofilled"},
            {"title": "Follow", "status": "Needs follow-up"},
            {"title": "Done", "status": "Applied"},
            {"title": "Skipped", "status": "Skipped"},
        ]

        grouped = lss.group_application_records(rows)

        assert [row["title"] for row in grouped["next"]] == ["New"]
        assert [row["title"] for row in grouped["progress"]] == ["Opened", "Autofilled"]
        assert [row["title"] for row in grouped["follow"]] == ["Follow"]
        assert [row["title"] for row in grouped["done"]] == ["Done"]
        assert [row["title"] for row in grouped["skipped"]] == ["Skipped"]

    def test_static_dashboard_has_dedicated_queue_column(self):
        html = (lss.STATIC_DIR / "index.html").read_text()

        assert 'class="queue-panel"' in html
        assert html.index('class="queue-panel"') < html.index('class="apply-panel"')

    def test_static_dashboard_merges_result_jobs_and_auto_advances(self):
        script = (lss.STATIC_DIR / "app.js").read_text()

        assert "mergeQueueRows" in script
        assert "jobfind:results-jobs" in script
        assert "autoSelectNextQueuedJob" in script
        assert "supportsIndeedAutofill" in script

    def test_static_dashboard_has_personal_keywords_field(self):
        html = (lss.STATIC_DIR / "index.html").read_text()
        script = (lss.STATIC_DIR / "app.js").read_text()

        assert 'name="personal_keywords"' in html
        assert "jobfind.personalKeywords" in script


class TestSavedAnswers:
    def test_save_overlay_answer_writes_json_and_markdown(self, tmp_path, monkeypatch):
        json_file = tmp_path / "saved_answers.json"
        md_file = tmp_path / "saved_answers.md"
        monkeypatch.setattr(lss, "SAVED_ANSWERS_FILE", json_file)
        monkeypatch.setattr(lss, "SAVED_ANSWERS_MD_FILE", md_file)

        saved, error = lss.save_overlay_answer(
            {
                "question": "Why do you want this job?",
                "answer": "I want to learn and help customers.",
                "kind": "text",
                "source": "edited",
                "job": {
                    "title": "Cashier",
                    "company": "Example Market",
                    "url": "https://www.indeed.com/viewjob?jk=abc",
                },
            }
        )

        assert error is None
        assert saved["question"] == "Why do you want this job?"
        payload = json.loads(json_file.read_text())
        assert payload["answers"][0]["answer"] == "I want to learn and help customers."
        markdown = md_file.read_text()
        assert "Why do you want this job?" in markdown
        assert "Example Market" in markdown

    def test_save_overlay_answer_updates_existing_question(self, tmp_path, monkeypatch):
        json_file = tmp_path / "saved_answers.json"
        md_file = tmp_path / "saved_answers.md"
        monkeypatch.setattr(lss, "SAVED_ANSWERS_FILE", json_file)
        monkeypatch.setattr(lss, "SAVED_ANSWERS_MD_FILE", md_file)

        lss.save_overlay_answer({"question": "Why this job?", "answer": "First answer", "kind": "text"})
        saved, error = lss.save_overlay_answer({"question": "Why this job?", "answer": "Better answer", "kind": "text"})

        assert error is None
        assert saved["answer"] == "Better answer"
        payload = json.loads(json_file.read_text())
        assert len(payload["answers"]) == 1
        assert payload["answers"][0]["times_saved"] == 2

    def test_save_overlay_answer_rejects_sensitive_question(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lss, "SAVED_ANSWERS_FILE", tmp_path / "saved_answers.json")
        monkeypatch.setattr(lss, "SAVED_ANSWERS_MD_FILE", tmp_path / "saved_answers.md")

        saved, error = lss.save_overlay_answer(
            {"question": "What is your Social Security Number?", "answer": "123", "kind": "text"}
        )

        assert saved is None
        assert "not safe" in error

    def test_profile_with_saved_answers_hydrates_exact_question_map(self, tmp_path, monkeypatch):
        json_file = tmp_path / "saved_answers.json"
        monkeypatch.setattr(lss, "SAVED_ANSWERS_FILE", json_file)
        lss.write_json_file(
            json_file,
            {
                "answers": [
                    {
                        "key": "why do you want this job",
                        "question": "Why do you want this job?",
                        "answer": "Saved answer",
                    }
                ]
            },
        )

        profile = lss.profile_with_saved_answers({"short_intro": "Profile answer"})

        assert profile["_saved_answers"] == {"why do you want this job": "Saved answer"}

    def test_overlay_enrichment_prefers_saved_answer_before_ai(self, tmp_path, monkeypatch):
        def fail_ai(payload, **kwargs):
            raise AssertionError("AI should not be called when a saved answer matches")

        def fail_load():
            raise AssertionError("saved answers should already be hydrated into the profile")

        monkeypatch.setattr(lss, "suggest_application_answer", fail_ai)
        monkeypatch.setattr(lss, "load_saved_answers", fail_load)
        report = {
            "current_step_review_items": [
                {"question": "Why do you want this job?", "kind": "text", "suggestable": True}
            ]
        }

        enriched = lss.enrich_review_items_for_overlay(
            report,
            {"title": "Cashier"},
            {"_saved_answers": {"why do you want this job": "Saved exact answer"}},
        )

        assert enriched[0]["suggestion"] == "Saved exact answer"
        assert enriched[0]["suggestion_source"] == "saved answer"

    def test_update_saved_answer_route_updates_existing_answer(self, tmp_path, monkeypatch):
        json_file = tmp_path / "saved_answers.json"
        md_file = tmp_path / "saved_answers.md"
        monkeypatch.setattr(lss, "SAVED_ANSWERS_FILE", json_file)
        monkeypatch.setattr(lss, "SAVED_ANSWERS_MD_FILE", md_file)
        lss.save_overlay_answer({"question": "Why this job?", "answer": "Old answer", "kind": "text"})

        class DummyHandler:
            path = "/api/saved-answers/update"

            def __init__(self):
                self.sent = None

            def read_json_body(self):
                return {"key": "why this job", "answer": "New answer", "autofill_enabled": False}, None

            def send_json(self, payload, status=200):
                self.sent = (payload, status)

            def send_error(self, *args):
                raise AssertionError(args)

        handler = DummyHandler()

        lss.SearchHandler.do_POST(handler)

        assert handler.sent[1] == 200
        assert handler.sent[0]["saved_answer"]["answer"] == "New answer"
        assert handler.sent[0]["saved_answer"]["autofill_enabled"] is False

    def test_delete_saved_answer_route_deletes_existing_answer(self, tmp_path, monkeypatch):
        json_file = tmp_path / "saved_answers.json"
        md_file = tmp_path / "saved_answers.md"
        monkeypatch.setattr(lss, "SAVED_ANSWERS_FILE", json_file)
        monkeypatch.setattr(lss, "SAVED_ANSWERS_MD_FILE", md_file)
        lss.save_overlay_answer({"question": "Why this job?", "answer": "Old answer", "kind": "text"})

        class DummyHandler:
            path = "/api/saved-answers/delete"

            def __init__(self):
                self.sent = None

            def read_json_body(self):
                return {"key": "why this job"}, None

            def send_json(self, payload, status=200):
                self.sent = (payload, status)

            def send_error(self, *args):
                raise AssertionError(args)

        handler = DummyHandler()

        lss.SearchHandler.do_POST(handler)

        assert handler.sent == ({"deleted": True}, 200)
        assert lss.load_saved_answers() == {"answers": []}

    def test_update_saved_answer_route_rejects_missing_key(self):
        class DummyHandler:
            path = "/api/saved-answers/update"

            def __init__(self):
                self.sent = None

            def read_json_body(self):
                return {"answer": "No key"}, None

            def send_json(self, payload, status=200):
                self.sent = (payload, status)

            def send_error(self, *args):
                raise AssertionError(args)

        handler = DummyHandler()

        lss.SearchHandler.do_POST(handler)

        assert handler.sent[1] == 400
        assert "key is required" in handler.sent[0]["error"]

    def test_manager_routes_do_not_get_overlay_cors(self):
        class DummyHandler:
            path = "/api/saved-answers/update"
            headers = {"Origin": "https://smartapply.indeed.com"}

            def __init__(self):
                self.headers_sent = []

            def send_header(self, name, value):
                self.headers_sent.append((name, value))

        handler = DummyHandler()

        lss.SearchHandler.add_overlay_cors_headers(handler)

        assert not any(name == "Access-Control-Allow-Origin" for name, _ in handler.headers_sent)


class TestApplicantProfile:
    def test_save_profile_updates_trimmed_fields_and_preserves_advanced_data(self, tmp_path, monkeypatch):
        profile_file = tmp_path / "applicant_profile.json"
        monkeypatch.setattr(lss, "PROFILE_FILE", profile_file)
        lss.write_json_file(
            profile_file,
            {
                "name": "Old Name",
                "email": "old@example.com",
                "custom_answers": {"Are you available weekends?": "Yes"},
                "favorite_color": "green",
            },
        )

        profile, warnings = lss.save_applicant_profile_updates(
            {
                "name": "  New Name  ",
                "email": " new@example.com ",
                "custom_answers": {"Should not": "overwrite"},
                "browser_state": "ignore me",
            }
        )

        assert profile["name"] == "New Name"
        assert profile["email"] == "new@example.com"
        assert profile["custom_answers"] == {"Are you available weekends?": "Yes"}
        assert profile["favorite_color"] == "green"
        assert "browser_state" not in profile
        assert "Resume path is not set" in warnings[0]

    def test_save_profile_warns_about_missing_resume_path_but_saves(self, tmp_path, monkeypatch):
        profile_file = tmp_path / "applicant_profile.json"
        monkeypatch.setattr(lss, "PROFILE_FILE", profile_file)

        profile, warnings = lss.save_applicant_profile_updates(
            {"resume_path": str(tmp_path / "missing.pdf")}
        )

        assert profile["resume_path"].endswith("missing.pdf")
        assert warnings == [f"Resume file missing: {tmp_path / 'missing.pdf'}"]
        saved = json.loads(profile_file.read_text())
        assert saved["resume_path"] == str(tmp_path / "missing.pdf")

    def test_read_json_body_rejects_non_object_payload(self):
        class DummyHandler:
            headers = {"Content-Length": "2"}
            rfile = io.BytesIO(b"[]")

        payload, error = lss.SearchHandler.read_json_body(DummyHandler())

        assert payload == {}
        assert error == "Request body must be a JSON object."


class TestCommonAnswers:
    def test_save_common_answers_preserves_existing_custom_and_unknown_keys(self, tmp_path, monkeypatch):
        profile_file = tmp_path / "applicant_profile.json"
        monkeypatch.setattr(lss, "PROFILE_FILE", profile_file)
        lss.write_json_file(
            profile_file,
            {
                "name": "Alex",
                "favorite_color": "green",
                "custom_answers": {
                    "Are you available weekends?": "Yes",
                    "Unusual question": "Keep me",
                },
            },
        )

        profile, answers, error = lss.save_common_answers_updates(
            {
                "answers": {
                    "ideal_job": "  A friendly part-time job where I can learn. ",
                    "transportation": " Yes, I can get to work reliably. ",
                    "browser_state": "ignore",
                }
            }
        )

        assert error is None
        assert profile["favorite_color"] == "green"
        assert profile["custom_answers"]["Unusual question"] == "Keep me"
        assert profile["custom_answers"]["What does your ideal job look like?"] == (
            "A friendly part-time job where I can learn."
        )
        assert answers["transportation"] == "Yes, I can get to work reliably."

    def test_common_answers_payload_rejects_non_object_answers(self):
        answers, error = lss.common_answers_payload({"answers": []})

        assert answers == {}
        assert error == "answers must be a JSON object."

    def test_common_answers_uses_profile_availability_fallback(self):
        answers = lss.common_answers_from_profile({"availability": "Weekends"})

        assert answers["availability"] == "Weekends"

    def test_ai_polish_without_provider_returns_ollama_setup_message(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(lss, "ollama_available", lambda: False)

        payload, status = lss.improve_common_answer({"key": "ideal_job", "draft": "I want to learn."})

        assert status == 503
        assert "ollama pull" in payload["error"]
        assert "sk-" not in payload["error"]

    def test_ollama_available_uses_tags_endpoint(self, monkeypatch):
        calls = []

        class Response:
            status_code = 200

        def fake_get(url, timeout):
            calls.append((url, timeout))
            return Response()

        monkeypatch.setattr(lss.requests, "get", fake_get)
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/")

        assert lss.ollama_available()
        assert calls == [("http://localhost:11434/api/tags", 0.7)]

    def test_provider_selection_prefers_openai(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_MODEL", "openai-test-model")
        monkeypatch.setattr(lss, "ollama_available", lambda: True)

        status = lss.ai_provider_status()

        assert status["enabled"]
        assert status["provider"] == "openai"
        assert status["model"] == "openai-test-model"

    def test_provider_selection_uses_ollama_without_openai_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
        monkeypatch.setattr(lss, "ollama_available", lambda: True)

        status = lss.ai_provider_status()

        assert status["enabled"]
        assert status["provider"] == "ollama"
        assert status["model"] == "local-test-model"

    def test_improve_common_answer_uses_ollama_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(lss, "ollama_available", lambda: True)
        monkeypatch.setattr(lss, "call_ollama_polish", lambda prompt, **kwargs: ({"suggestion": "Better answer", "provider": "ollama"}, 200))

        payload, status = lss.improve_common_answer({"key": "ideal_job", "draft": "I want to learn."})

        assert status == 200
        assert payload["suggestion"] == "Better answer"
        assert payload["provider"] == "ollama"

    def test_improve_common_answer_openai_wins_when_key_exists(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(lss, "ollama_available", lambda: True)
        monkeypatch.setattr(lss, "call_openai_polish", lambda prompt: ({"suggestion": "OpenAI answer", "provider": "openai"}, 200))

        payload, status = lss.improve_common_answer({"key": "ideal_job", "draft": "I want to learn."})

        assert status == 200
        assert payload["provider"] == "openai"

    def test_ollama_request_uses_safe_prompt_and_stream_false(self, monkeypatch):
        calls = []

        class Response:
            status_code = 200

            def json(self):
                return {"response": "A clearer answer."}

        def fake_post(url, json, timeout):
            calls.append((url, json, timeout))
            return Response()

        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/")
        monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
        monkeypatch.setattr(lss.requests, "post", fake_post)

        payload, status = lss.call_ollama_polish("Keep the student's meaning.")

        assert status == 200
        assert payload["suggestion"] == "A clearer answer."
        assert payload["provider"] == "ollama"
        url, body, timeout = calls[0]
        assert url == "http://localhost:11434/api/generate"
        assert body["model"] == "local-test-model"
        assert body["stream"] is False
        assert "Keep the student's meaning." in body["prompt"]
        assert timeout == 45

    def test_extract_ollama_text(self):
        assert lss.extract_ollama_text({"response": "  Better wording.  "}) == "Better wording."

    def test_build_ai_polish_prompt_includes_job_context(self):
        prompt, error = lss.build_ai_polish_prompt(
            {
                "key": "why_this_company",
                "draft": "I like helping people.",
                "job": {
                    "title": "Cashier",
                    "company": "Example Market",
                    "description": "Help customers and keep the store organized.",
                },
            },
            {"short_intro": "I am a high school student."},
        )

        assert error is None
        assert "Cashier" in prompt
        assert "Example Market" in prompt
        assert "Do not invent" in prompt

    def test_build_question_suggestion_prompt_includes_context_and_common_answers(self):
        prompt, error = lss.build_question_suggestion_prompt(
            {
                "question": "Do you have any service experience?",
                "job": {
                    "title": "Cashier",
                    "company": "Example Market",
                    "description": "Help customers and keep the store organized.",
                },
            },
            {
                "short_intro": "I am dependable.",
                "custom_answers": {
                    "Tell us about your customer service experience.": "I help people patiently.",
                },
            },
        )

        assert error is None
        assert "Do you have any service experience?" in prompt
        assert "Cashier" in prompt
        assert "I help people patiently." in prompt
        assert "Do not invent work experience" in prompt
        assert "If the question asks about service" in prompt

    def test_question_suggestion_rejects_sensitive_and_legal_questions(self):
        prompt, error = lss.build_question_suggestion_prompt(
            {"question": "What is your Social Security Number?"},
            {},
        )
        assert prompt == ""
        assert "should not be answered by AI" in error

        prompt, error = lss.build_question_suggestion_prompt(
            {"question": "Are you legally authorized to work?"},
            {},
        )
        assert prompt == ""
        assert "should not be answered by AI" in error

    def test_question_suggestion_accepts_structured_review_item(self):
        prompt, error = lss.build_question_suggestion_prompt(
            {
                "review_item": {
                    "question": "Do you have any service experience?",
                    "kind": "text",
                    "suggestable": True,
                },
                "job": {"title": "Cashier"},
            },
            {
                "short_intro": "I am dependable.",
                "custom_answers": {"Tell us about your customer service experience.": "I help people patiently."},
            },
        )

        assert error is None
        assert "Do you have any service experience?" in prompt

    def test_question_suggestion_rejects_unsuggestable_review_item(self):
        prompt, error = lss.build_question_suggestion_prompt(
            {
                "review_item": {
                    "question": "Resume upload",
                    "kind": "resume",
                    "suggestable": False,
                }
            },
            {},
        )

        assert prompt == ""
        assert "should not be answered by AI" in error

    def test_suggest_application_answer_uses_ollama_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(lss, "ollama_available", lambda: True)
        monkeypatch.setattr(lss, "call_ollama_polish", lambda prompt, **kwargs: ({"suggestion": "I do not have formal service experience yet, but I am dependable.", "provider": "ollama"}, 200))

        payload, status = lss.suggest_application_answer(
            {"question": "Do you have any service experience?", "job": {"title": "Cashier"}}
        )

        assert status == 200
        assert payload["provider"] == "ollama"
        assert "dependable" in payload["suggestion"]

    def test_suggest_with_configured_provider_returns_disabled_message(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(lss, "ollama_available", lambda: False)

        payload, status = lss.suggest_with_configured_provider("hello")

        assert status == 503
        assert payload["provider"] == "none"

    def test_overlay_enrichment_prefers_common_answers(self, monkeypatch):
        def fail_ai(payload):
            raise AssertionError("AI should not be called when a common answer matches")

        monkeypatch.setattr(lss, "suggest_application_answer", fail_ai)
        item = {
            "question": "Do you have any service experience?",
            "kind": "text",
            "suggestable": True,
        }
        report = {"current_step_review_items": [item]}

        enriched = lss.enrich_review_items_for_overlay(
            report,
            {"title": "Cashier"},
            {
                "custom_answers": {
                    "Tell us about your customer service experience.": "I help people patiently."
                }
            },
        )

        assert enriched[0]["suggestion"] == "I help people patiently."
        assert enriched[0]["suggestion_source"].startswith("common answer")

    def test_overlay_enrichment_calls_ai_only_for_suggestable_items(self, monkeypatch):
        calls = []

        def fake_ai(payload, **kwargs):
            calls.append(payload)
            return {"suggestion": "AI answer", "provider": "ollama"}, 200

        monkeypatch.setattr(lss, "suggest_application_answer", fake_ai)
        report = {
            "current_step_review_items": [
                {"question": "Why do you want this job?", "kind": "text", "suggestable": True},
                {"question": "Social Security Number", "kind": "text", "suggestable": False},
            ]
        }

        enriched = lss.enrich_review_items_for_overlay(report, {"title": "Cashier"}, {})

        assert len(calls) == 1
        assert enriched[0]["suggestion"] == "AI answer"
        assert "suggestion" not in enriched[1]

    def test_overlay_enrichment_keeps_usable_items_without_ai_provider(self, monkeypatch):
        monkeypatch.setattr(
            lss,
            "suggest_application_answer",
            lambda payload, **kwargs: ({"error": "AI polish is optional. Install Ollama to enable it."}, 503),
        )
        report = {
            "current_step_review_items": [
                {"question": "Why do you want this job?", "kind": "text", "suggestable": True}
            ]
        }

        enriched = lss.enrich_review_items_for_overlay(report, {"title": "Cashier"}, {})

        assert enriched[0]["question"] == "Why do you want this job?"
        assert "Install Ollama" in enriched[0]["suggestion_error"]

    def test_overlay_enrichment_skips_ai_for_radio_select_and_checkbox(self, monkeypatch):
        def fail_ai(payload, **kwargs):
            raise AssertionError("AI should not be called for non-text review items")

        monkeypatch.setattr(lss, "suggest_application_answer", fail_ai)
        report = {
            "current_step_review_items": [
                {"question": "Yes/No question on this step", "kind": "radio", "suggestable": False, "options": ["Yes", "No"]},
                {"question": "Pick a shift", "kind": "select", "suggestable": True, "options": ["Morning", "Evening"]},
                {"question": "Choose available days", "kind": "checkbox", "suggestable": True, "options": ["Sat", "Sun"]},
            ]
        }

        enriched = lss.enrich_review_items_for_overlay(report, {"title": "Cashier"}, {})

        assert all("suggestion" not in item for item in enriched)

    def test_overlay_enrichment_caps_auto_ai_suggestions(self, monkeypatch):
        calls = []

        def fake_ai(payload, **kwargs):
            calls.append((payload, kwargs))
            return {"suggestion": f"Answer {len(calls)}", "provider": "ollama"}, 200

        monkeypatch.setattr(lss, "suggest_application_answer", fake_ai)
        report = {
            "current_step_review_items": [
                {"question": "Why do you want this job?", "kind": "text", "suggestable": True},
                {"question": "What is your ideal job?", "kind": "text", "suggestable": True},
                {"question": "Tell us about yourself.", "kind": "text", "suggestable": True},
            ]
        }

        enriched = lss.enrich_review_items_for_overlay(report, {"title": "Cashier"}, {}, ai_limit=2, ollama_timeout=7)

        assert len(calls) == 2
        assert calls[0][1]["ollama_timeout"] == 7
        assert enriched[0]["suggestion"] == "Answer 1"
        assert enriched[1]["suggestion"] == "Answer 2"
        assert "limited" in enriched[2]["suggestion_error"]

    def test_resource_snapshot_identifies_jobfind_processes(self, monkeypatch):
        output = """
        100  4.5  1.2 /usr/bin/python3 pipeline/local_search_server.py
        101  3.0  2.1 /Applications/Google Chrome --user-data-dir=/Users/warrenjung/project/jobFind/data/browser_profiles/indeed
        102  2.0 29.0 /Applications/Ollama.app/Contents/Resources/llama-server --model test
        103  1.0  0.1 /bin/zsh
        """
        monkeypatch.setattr(lss.subprocess, "check_output", lambda *args, **kwargs: output)

        rows = lss.jobfind_resource_snapshot()

        assert [row["kind"] for row in rows] == ["python", "managed_chrome", "ollama"]


class TestSetupStatus:
    def _isolate_setup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lss, "PROFILE_FILE", tmp_path / "applicant_profile.json")
        monkeypatch.setattr(lss, "RESULTS_FILE", tmp_path / "jobs_clean.html")
        monkeypatch.setattr(lss, "latest_login_port", lambda: None)
        monkeypatch.setattr(lss, "SESSION_RECOVERY_MESSAGE", "")

    def test_missing_profile_reports_incomplete_profile(self, tmp_path, monkeypatch):
        self._isolate_setup(tmp_path, monkeypatch)

        status = lss.build_setup_status()

        assert not status["all_ready"]
        assert not status["profile_exists"]
        assert not status["checks"]["profile"]["ready"]
        assert "name" in status["checks"]["profile"]["message"]

    def test_complete_profile_reports_profile_ready(self, tmp_path, monkeypatch):
        self._isolate_setup(tmp_path, monkeypatch)
        lss.write_json_file(
            lss.PROFILE_FILE,
            {
                "first_name": "Alex",
                "last_name": "Rivera",
                "email": "alex@example.com",
                "phone": "555-0100",
                "city": "Cupertino",
                "state": "CA",
                "availability": "Weekends",
            },
        )

        status = lss.build_setup_status()

        assert status["profile_exists"]
        assert status["checks"]["profile"]["ready"]

    def test_missing_resume_path_reports_resume_warning(self, tmp_path, monkeypatch):
        self._isolate_setup(tmp_path, monkeypatch)
        lss.write_json_file(lss.PROFILE_FILE, {"name": "Alex"})

        status = lss.build_setup_status()

        assert not status["checks"]["resume"]["ready"]
        assert "Resume path is not set" in status["checks"]["resume"]["message"]

    def test_existing_resume_file_reports_resume_ready(self, tmp_path, monkeypatch):
        self._isolate_setup(tmp_path, monkeypatch)
        resume = tmp_path / "resume.pdf"
        resume.write_text("fake pdf")
        lss.write_json_file(lss.PROFILE_FILE, {"name": "Alex", "resume_path": str(resume)})

        status = lss.build_setup_status()

        assert status["checks"]["resume"]["ready"]

    def test_missing_results_file_reports_no_results(self, tmp_path, monkeypatch):
        self._isolate_setup(tmp_path, monkeypatch)

        status = lss.build_setup_status()

        assert not status["checks"]["results"]["ready"]
        assert "Run a search" in status["checks"]["results"]["message"]

    def test_results_file_reports_results_ready(self, tmp_path, monkeypatch):
        self._isolate_setup(tmp_path, monkeypatch)
        lss.RESULTS_FILE.write_text("<html></html>")

        status = lss.build_setup_status()

        assert status["checks"]["results"]["ready"]

    def test_live_chrome_port_reports_indeed_login_ready(self, tmp_path, monkeypatch):
        self._isolate_setup(tmp_path, monkeypatch)
        monkeypatch.setattr(lss, "latest_login_port", lambda: 9222)

        status = lss.build_setup_status()

        assert status["checks"]["indeed_login"]["ready"]


class TestAutofillLoginFlow:
    def test_indeed_profile_path_stays_under_ignored_data(self):
        assert lss.INDEED_PROFILE_DIR == lss.DATA_DIR / "browser_profiles" / "indeed"

    def test_report_needs_login_from_stage_or_reason(self):
        assert lss.report_needs_login({"stages": ["login_required"]})
        assert lss.report_needs_login({"status_reason": "Account verification required after login."})
        assert not lss.report_needs_login({"stages": ["opened_application_form"]})

    def test_resume_request_requires_selected_job_url(self):
        class DummyHandler:
            def __init__(self):
                self.response = None

            def send_json(self, payload, status=200):
                self.response = (payload, status)

        handler = DummyHandler()

        lss.SearchHandler.handle_autofill_request(handler, {"title": "Cashier"}, resume=True)

        assert handler.response is not None
        payload, status = handler.response
        assert status == 400
        assert "valid apply URL" in payload["error"]

    def test_overlay_resume_post_routes_to_resume_autofill(self):
        class DummyHandler:
            path = "/api/applications/autofill/overlay-resume"

            def __init__(self):
                self.called = None

            def read_json_body(self):
                return {"url": "https://www.indeed.com/viewjob?jk=abc", "title": "Cashier"}, None

            def handle_autofill_request(self, payload, resume=False):
                self.called = (payload, resume)

            def send_error(self, *args):
                raise AssertionError(args)

        handler = DummyHandler()

        lss.SearchHandler.do_POST(handler)

        assert handler.called == (
            {"url": "https://www.indeed.com/viewjob?jk=abc", "title": "Cashier"},
            True,
        )

    def test_overlay_resume_cors_allows_indeed_origins(self):
        class DummyHandler:
            path = "/api/applications/autofill/overlay-resume"
            headers = {"Origin": "https://smartapply.indeed.com"}

            def __init__(self):
                self.headers_sent = []

            def send_header(self, name, value):
                self.headers_sent.append((name, value))

        handler = DummyHandler()

        lss.SearchHandler.add_overlay_cors_headers(handler)

        assert ("Access-Control-Allow-Origin", "https://smartapply.indeed.com") in handler.headers_sent

    def test_overlay_cors_allows_localhost_dev_origin(self):
        class DummyHandler:
            path = "/api/saved-answers"
            headers = {"Origin": "http://localhost:8000"}

            def __init__(self):
                self.headers_sent = []

            def send_header(self, name, value):
                self.headers_sent.append((name, value))

        handler = DummyHandler()

        lss.SearchHandler.add_overlay_cors_headers(handler)

        assert ("Access-Control-Allow-Origin", "http://localhost:8000") in handler.headers_sent

    def test_overlay_cors_rejects_file_origin(self):
        class DummyHandler:
            path = "/api/saved-answers"
            headers = {"Origin": "file://"}

            def __init__(self):
                self.headers_sent = []

            def send_header(self, name, value):
                self.headers_sent.append((name, value))

        handler = DummyHandler()

        lss.SearchHandler.add_overlay_cors_headers(handler)

        assert not any(name == "Access-Control-Allow-Origin" for name, _ in handler.headers_sent)

    def test_latest_login_port_returns_running_chrome(self, monkeypatch):
        class ExitedProc:
            def poll(self):
                return 0  # already exited

        class RunningProc:
            def poll(self):
                return None  # still running

        monkeypatch.setattr(
            lss.application_autofill, "chrome_debug_ready", lambda port: True
        )
        monkeypatch.setattr(
            lss,
            "LIVE_LOGIN_REPORTS",
            [
                {"_debug_port": 9333, "_chrome_proc": ExitedProc()},
                {"_debug_port": 9444, "_chrome_proc": RunningProc()},
            ],
        )

        assert lss.latest_login_port() == 9444

    def test_latest_login_port_none_when_endpoint_dead(self, tmp_path, monkeypatch):
        class RunningProc:
            def poll(self):
                return None

        monkeypatch.setattr(lss, "INDEED_PROFILE_DIR", tmp_path / "indeed")
        monkeypatch.setattr(
            lss.application_autofill, "chrome_debug_ready", lambda port: False
        )
        monkeypatch.setattr(
            lss, "LIVE_LOGIN_REPORTS", [{"_debug_port": 9444, "_chrome_proc": RunningProc()}]
        )

        assert lss.latest_login_port() is None

    def test_latest_login_port_uses_saved_session_after_restart(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "indeed"
        ab.save_chrome_session(profile_dir, 9555)
        monkeypatch.setattr(lss, "INDEED_PROFILE_DIR", profile_dir)
        monkeypatch.setattr(lss, "LIVE_LOGIN_REPORTS", [])
        monkeypatch.setattr(lss.application_autofill, "chrome_debug_ready", lambda port: port == 9555)

        assert lss.latest_login_port() == 9555

    def test_latest_login_port_ignores_dead_saved_session(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "indeed"
        ab.save_chrome_session(profile_dir, 9555)
        monkeypatch.setattr(lss, "INDEED_PROFILE_DIR", profile_dir)
        monkeypatch.setattr(lss, "LIVE_LOGIN_REPORTS", [])
        monkeypatch.setattr(lss.application_autofill, "chrome_debug_ready", lambda port: False)

        assert lss.latest_login_port() is None

    def test_latest_login_port_recovers_live_default_port(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "indeed"
        monkeypatch.setattr(lss, "INDEED_PROFILE_DIR", profile_dir)
        monkeypatch.setattr(lss, "LIVE_LOGIN_REPORTS", [])
        monkeypatch.setattr(ab, "chrome_debug_ready", lambda port: port == 9222)
        monkeypatch.setattr(
            ab,
            "chrome_process_uses_profile",
            lambda port, path: port == 9222 and path == profile_dir,
        )

        assert lss.latest_login_port() == 9222
        assert lss.SESSION_RECOVERY_MESSAGE == "Recovered existing JobFind Chrome session."
        assert ab.load_chrome_session(profile_dir)["debug_port"] == 9222

    def test_latest_login_port_reports_profile_lock_without_debug_endpoint(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "indeed"
        profile_dir.mkdir(parents=True)
        (profile_dir / "SingletonLock").write_text("locked")
        monkeypatch.setattr(lss, "INDEED_PROFILE_DIR", profile_dir)
        monkeypatch.setattr(lss, "LIVE_LOGIN_REPORTS", [])
        monkeypatch.setattr(lss.application_autofill, "chrome_debug_ready", lambda port: False)

        assert lss.latest_login_port() is None
        assert "Close JobFind Chrome windows" in lss.SESSION_RECOVERY_MESSAGE


class TestStaticFrontend:
    def test_app_config_keys(self):
        cfg = lss.app_config()
        assert cfg["default_location"] == lss.DEFAULT_LOCATION
        assert cfg["default_radius"] == lss.DEFAULT_RADIUS
        assert cfg["default_min_score"] == lss.DEFAULT_MIN_SCORE
        assert cfg["radius_choices"] == ["5", "10", "15", "25", "35", "50"]

    def test_static_files_exist(self):
        for name in ("index.html", "app.css", "app.js"):
            assert (lss.STATIC_DIR / name).is_file()

    def test_index_links_external_assets_and_has_no_inline_blocks(self):
        index = (lss.STATIC_DIR / "index.html").read_text()
        assert '/app.css' in index and '/app.js' in index
        # The frontend was extracted out of the f-string: no giant inline blocks.
        assert "<style>" not in index
        assert "<script>" not in index

    def test_static_allowlist_routes(self):
        assert set(lss.STATIC_FILES) == {"/", "/index.html", "/app.css", "/app.js"}
        # Only known names are served (no arbitrary paths).
        for _, (name, _ctype) in lss.STATIC_FILES.items():
            assert name in {"index.html", "app.css", "app.js"}


class TestReadBinaryBody:
    def test_enforces_cap(self):
        class DummyHandler:
            headers = {"Content-Length": "10"}
            rfile = io.BytesIO(b"0123456789")

        data, error = lss.SearchHandler.read_binary_body(DummyHandler(), 5)
        assert data == b""
        assert "too large" in error

    def test_reads_bytes(self):
        class DummyHandler:
            headers = {"Content-Length": "5"}
            rfile = io.BytesIO(b"hello")

        data, error = lss.SearchHandler.read_binary_body(DummyHandler(), 100)
        assert data == b"hello"
        assert error is None

    def test_rejects_empty_body(self):
        class DummyHandler:
            headers = {"Content-Length": "0"}
            rfile = io.BytesIO(b"")

        data, error = lss.SearchHandler.read_binary_body(DummyHandler(), 100)
        assert data == b""
        assert "empty" in error


class TestResumeUpload:
    def _handler(self, path, binary_result):
        class DummyHandler:
            def __init__(self):
                self.sent = None

            def read_binary_body(self, _max_bytes):
                return binary_result

            def send_json(self, payload, status=200):
                self.sent = (payload, status)

        handler = DummyHandler()
        handler.path = path
        return handler

    def test_upload_saves_file_and_updates_profile(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lss, "RESUMES_DIR", tmp_path / "resumes")
        monkeypatch.setattr(lss, "PROFILE_FILE", tmp_path / "profile.json")
        handler = self._handler(
            "/api/applicant-profile/upload-resume?filename=Alex%20Resume.pdf",
            (b"%PDF-1.4 data", None),
        )

        lss.SearchHandler.handle_resume_upload(handler)

        payload, status = handler.sent
        assert status == 200
        assert payload["resume_filename"] == "Alex_Resume.pdf"
        saved = tmp_path / "resumes" / "Alex_Resume.pdf"
        assert saved.read_bytes() == b"%PDF-1.4 data"
        assert payload["resume_path"] == str(saved.resolve())
        assert payload["profile"]["resume_path"] == str(saved.resolve())
        # File exists, so there is no missing-resume warning.
        assert payload["warnings"] == []

    def test_upload_rejects_bad_extension_before_reading_body(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lss, "RESUMES_DIR", tmp_path / "resumes")
        read_called = {"value": False}

        class DummyHandler:
            path = "/api/applicant-profile/upload-resume?filename=evil.exe"

            def __init__(self):
                self.sent = None

            def read_binary_body(self, _max_bytes):
                read_called["value"] = True
                return b"x", None

            def send_json(self, payload, status=200):
                self.sent = (payload, status)

        handler = DummyHandler()
        lss.SearchHandler.handle_resume_upload(handler)

        assert handler.sent[1] == 400
        assert read_called["value"] is False
        assert not (tmp_path / "resumes").exists()

    def test_upload_propagates_oversized_body_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lss, "RESUMES_DIR", tmp_path / "resumes")
        handler = self._handler(
            "/api/applicant-profile/upload-resume?filename=ok.pdf",
            (b"", "Uploaded file is too large."),
        )

        lss.SearchHandler.handle_resume_upload(handler)

        assert handler.sent[1] == 400
        assert "too large" in handler.sent[0]["error"]
        assert not (tmp_path / "resumes").exists()
