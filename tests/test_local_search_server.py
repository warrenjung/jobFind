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
