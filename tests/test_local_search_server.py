"""Unit tests for the pure helpers in local_search_server.py."""

import io
import json

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
        monkeypatch.setattr(lss, "call_ollama_polish", lambda prompt: ({"suggestion": "Better answer", "provider": "ollama"}, 200))

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

    def test_suggest_application_answer_uses_ollama_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(lss, "ollama_available", lambda: True)
        monkeypatch.setattr(lss, "call_ollama_polish", lambda prompt: ({"suggestion": "I do not have formal service experience yet, but I am dependable.", "provider": "ollama"}, 200))

        payload, status = lss.suggest_application_answer(
            {"question": "Do you have any service experience?", "job": {"title": "Cashier"}}
        )

        assert status == 200
        assert payload["provider"] == "ollama"
        assert "dependable" in payload["suggestion"]


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

    def test_latest_login_port_none_when_endpoint_dead(self, monkeypatch):
        class RunningProc:
            def poll(self):
                return None

        monkeypatch.setattr(
            lss.application_autofill, "chrome_debug_ready", lambda port: False
        )
        monkeypatch.setattr(
            lss, "LIVE_LOGIN_REPORTS", [{"_debug_port": 9444, "_chrome_proc": RunningProc()}]
        )

        assert lss.latest_login_port() is None

    def test_latest_login_port_uses_saved_session_after_restart(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "indeed"
        lss.application_autofill.save_chrome_session(profile_dir, 9555)
        monkeypatch.setattr(lss, "INDEED_PROFILE_DIR", profile_dir)
        monkeypatch.setattr(lss, "LIVE_LOGIN_REPORTS", [])
        monkeypatch.setattr(lss.application_autofill, "chrome_debug_ready", lambda port: port == 9555)

        assert lss.latest_login_port() == 9555

    def test_latest_login_port_ignores_dead_saved_session(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "indeed"
        lss.application_autofill.save_chrome_session(profile_dir, 9555)
        monkeypatch.setattr(lss, "INDEED_PROFILE_DIR", profile_dir)
        monkeypatch.setattr(lss, "LIVE_LOGIN_REPORTS", [])
        monkeypatch.setattr(lss.application_autofill, "chrome_debug_ready", lambda port: False)

        assert lss.latest_login_port() is None

    def test_latest_login_port_recovers_live_default_port(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "indeed"
        monkeypatch.setattr(lss, "INDEED_PROFILE_DIR", profile_dir)
        monkeypatch.setattr(lss, "LIVE_LOGIN_REPORTS", [])
        monkeypatch.setattr(lss.application_autofill, "chrome_debug_ready", lambda port: port == 9222)
        monkeypatch.setattr(
            lss.application_autofill,
            "chrome_process_uses_profile",
            lambda port, path: port == 9222 and path == profile_dir,
        )

        assert lss.latest_login_port() == 9222
        assert lss.SESSION_RECOVERY_MESSAGE == "Recovered existing JobFind Chrome session."
        assert lss.application_autofill.load_chrome_session(profile_dir)["debug_port"] == 9222

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
