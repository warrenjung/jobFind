"""Unit tests for application_autofill.py."""

from pathlib import Path

import pytest

import application_autofill as aa


PROFILE = {
    "name": "Alex Rivera",
    "email": "alex@example.com",
    "phone": "555-0148",
    "school": "Cupertino High School",
    "availability": "Weekdays after 3:30 PM and weekends",
    "work_eligibility": "Authorized to work in the United States",
    "short_intro": "I am dependable and excited to help customers.",
    "custom_answers": {
        "Do you have reliable transportation?": "Yes, I can get to work reliably.",
        "How many hours per week can you work?": "15-20 hours per week",
        "What does your ideal job look like?": "A part-time role where I can learn and help customers.",
        "Why do you want to work here?": "I like that this job lets me help people and build work experience.",
        "Tell us about your customer service experience.": "I try to listen carefully and stay patient when helping people.",
        "Tell us about a time you worked on a team.": "I communicate clearly and do my part on team projects.",
    },
}


class TestProfilePreparation:
    def test_splits_name_for_fallbacks(self):
        profile = aa.prepared_profile({"name": "Alex Rivera"})
        assert profile["first_name"] == "Alex"
        assert profile["last_name"] == "Rivera"

    def test_explicit_first_name_wins(self):
        profile = aa.prepared_profile({"name": "Alex Rivera", "first_name": "Lex"})
        assert profile["first_name"] == "Lex"


class TestAnswerMatching:
    def test_matches_email_phone_school_and_intro(self):
        assert aa.answer_for_prompt("Email address", PROFILE)[0] == "alex@example.com"
        assert aa.answer_for_prompt("Mobile phone", PROFILE)[0] == "555-0148"
        assert aa.answer_for_prompt("High school", PROFILE)[0] == "Cupertino High School"
        assert aa.answer_for_prompt("Why are you interested?", PROFILE)[0].startswith("I like")

    def test_custom_answer_wins(self):
        answer, reason = aa.answer_for_prompt("Do you have reliable transportation?", PROFILE)
        assert answer == "Yes, I can get to work reliably."
        assert "custom" in reason

    def test_sensitive_question_needs_review(self):
        answer, reason = aa.answer_for_prompt("What is your Social Security Number?", PROFILE)
        assert answer is None
        assert "sensitive" in reason

    def test_legal_question_uses_explicit_profile_value(self):
        answer, reason = aa.answer_for_prompt("Are you legally authorized to work?", PROFILE)
        assert answer == "Authorized to work in the United States"
        assert "explicit" in reason

    def test_legal_question_without_profile_needs_review(self):
        answer, reason = aa.answer_for_prompt("Will you require visa sponsorship?", {})
        assert answer is None
        assert "legal" in reason

    def test_common_answers_match_application_wording(self):
        assert aa.answer_for_prompt("What does your ideal job look like?", PROFILE)[0] == (
            "A part-time role where I can learn and help customers."
        )
        assert aa.answer_for_prompt("Why do you want to work here?", PROFILE)[0].startswith("I like")
        assert aa.answer_for_prompt("Tell us about your customer service experience", PROFILE)[0].startswith("I try")
        assert aa.answer_for_prompt("Do you have any service experience?", PROFILE)[0].startswith("I try")
        assert aa.answer_for_prompt("Describe any food service or retail service experience", PROFILE)[0].startswith("I try")
        assert aa.answer_for_prompt("Describe teamwork with coworkers", PROFILE)[0].startswith("I communicate")

    def test_common_answers_do_not_override_sensitive_questions(self):
        profile = {
            "custom_answers": {
                "Anything else an employer should know?": "My SSN is 123",
            }
        }

        answer, reason = aa.answer_for_prompt("What is your Social Security Number?", profile)

        assert answer is None
        assert "sensitive" in reason


class TestSelectMatching:
    def test_select_matches_profile_option(self):
        answer, reason = aa.select_answer_for_prompt(
            "Highest education level",
            {"education_level": "High school"},
            ["College", "High school", "Middle school"],
        )
        assert answer == "High school"
        assert "matched" in reason

    def test_select_without_match_needs_review(self):
        answer, reason = aa.select_answer_for_prompt("State", {"state": "CA"}, ["NY", "TX"])
        assert answer is None
        assert "review" in reason


class TestSafety:
    def test_submit_like_detection(self):
        assert aa.is_submit_like("Submit application")
        assert aa.is_submit_like("Continue")
        assert not aa.is_submit_like("Copy")

    def test_search_prompt_detection(self):
        assert aa.is_search_prompt("what: job title, keywords, or company")
        assert aa.is_search_prompt("where: city, state, or zip code")
        assert not aa.is_search_prompt("First name")

    def test_apply_button_detection(self):
        assert aa.is_apply_control_text("Apply now")
        assert aa.is_apply_control_text("Apply on company site")
        assert not aa.is_apply_control_text("Submit application")

    def test_blocked_page_detection(self):
        assert aa.is_blocked_page("Blocked - Indeed.com", "")
        assert not aa.is_blocked_page("Cashier Job", "Apply now")


class TestFakeFormAutofill:
    def test_autofills_fake_form_without_submitting(self):
        pytest.importorskip("playwright.sync_api")
        fixture = Path(__file__).parent / "fixtures" / "fake_application.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            {**PROFILE, "education_level": "High school"},
            headless=True,
            timeout_ms=15_000,
        )
        page = report["_page"]
        try:
            assert page.locator("#first_name").input_value() == "Alex"
            assert page.locator("#last_name").input_value() == "Rivera"
            assert page.locator("#email").input_value() == "alex@example.com"
            assert page.locator("#phone").input_value() == "555-0148"
            assert page.locator("#school").input_value() == "Cupertino High School"
            assert page.locator("#education").input_value() == "High school"
            assert page.locator("#intro").input_value().startswith("I like")
            assert page.locator("input[name='transportation'][value='yes']").is_checked()
            assert not page.locator("#terms").is_checked()
            assert page.locator("#ssn").input_value() == ""
            assert page.locator("#submitted").input_value() == "no"
            assert report["submitted"] is False
            assert report["filled_count"] >= 8
            assert any("Checkbox" in item or "accurate" in item for item in report["needs_review"])
        finally:
            aa.close_report_browser(report)

    def test_job_detail_clicks_apply_ignores_search_then_fills_form(self):
        pytest.importorskip("playwright.sync_api")
        fixture = Path(__file__).parent / "fixtures" / "fake_job_detail.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            PROFILE,
            headless=True,
            timeout_ms=15_000,
        )
        page = report["_page"]
        try:
            assert "opened_job_page" in report["stages"]
            assert "clicked_apply" in report["stages"]
            assert "opened_application_form" in report["stages"]
            assert "filled_fields" in report["stages"]
            assert page.locator("#what").input_value() == ""
            assert page.locator("#where").input_value() == ""
            assert page.locator("#first_name").input_value() == "Alex"
            assert page.locator("#last_name").input_value() == "Rivera"
            assert page.locator("#email").input_value() == "alex@example.com"
            assert page.locator("#submitted").input_value() == "no"
            assert any("skipped search field" in item for item in report["skipped"])
        finally:
            aa.close_report_browser(report)

    def test_persistent_profile_autofills_fake_form(self, tmp_path):
        pytest.importorskip("playwright.sync_api")
        fixture = Path(__file__).parent / "fixtures" / "fake_application.html"
        profile_dir = aa.persistent_profile_path(tmp_path, "indeed")
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            PROFILE,
            headless=True,
            timeout_ms=15_000,
            user_data_dir=profile_dir,
        )
        page = report["_page"]
        try:
            assert profile_dir.exists()
            assert report["persistent_profile"] == str(profile_dir)
            assert page.locator("#first_name").input_value() == "Alex"
            assert page.locator("#email").input_value() == "alex@example.com"
            assert report["submitted"] is False
        finally:
            aa.close_report_browser(report)

    def test_login_required_stage_before_resume(self):
        pytest.importorskip("playwright.sync_api")
        fixture = Path(__file__).parent / "fixtures" / "fake_login_required.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            PROFILE,
            headless=True,
            timeout_ms=15_000,
        )
        try:
            assert "login_required" in report["stages"]
            assert report["filled_count"] == 0
            assert "login" in report["status_reason"].lower()
        finally:
            aa.close_report_browser(report)


class TestApplyButtonMatching:
    def test_accepts_apply_variants(self):
        assert aa.is_apply_control_text("Apply now")
        assert aa.is_apply_control_text("Apply on company site")
        assert aa.is_apply_control_text("Apply")
        assert aa.is_apply_control_text("Easily apply")

    def test_rejects_non_apply_controls(self):
        assert not aa.is_apply_control_text("Apply filters")
        assert not aa.is_apply_control_text("Applied")
        assert not aa.is_apply_control_text("Save job")
        assert not aa.is_apply_control_text("")


class TestIframeApplyFlow:
    def test_finds_apply_button_in_iframe_then_fills_form(self):
        pytest.importorskip("playwright.sync_api")
        fixture = Path(__file__).parent / "fixtures" / "fake_job_detail_iframe.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            PROFILE,
            headless=True,
            timeout_ms=15_000,
        )
        try:
            # The Apply button lived in an iframe; it must still be clicked.
            assert "clicked_apply" in report["stages"]
            assert "opened_application_form" in report["stages"]
            assert report["submitted"] is False

            page = report["_page"]
            widget = next(
                frame for frame in page.frames if frame.url.endswith("fake_apply_widget.html")
            )
            assert widget.locator("#first_name").input_value() == "Alex"
            assert widget.locator("#email").input_value() == "alex@example.com"
            assert widget.locator("#submitted").input_value() == "no"
            # The outer job-search inputs stayed untouched.
            assert page.main_frame.locator("#what").input_value() == ""
            assert page.main_frame.locator("#where").input_value() == ""
        finally:
            aa.close_report_browser(report)


class TestActionButtonClassification:
    def test_advance_buttons(self):
        assert aa.classify_action_button("Continue") == "advance"
        assert aa.classify_action_button("Next") == "advance"
        assert aa.classify_action_button("Save and continue") == "advance"

    def test_submit_buttons_never_advance(self):
        assert aa.classify_action_button("Submit application") == "submit"
        assert aa.classify_action_button("Submit your application") == "submit"

    def test_never_click_buttons(self):
        assert aa.classify_action_button("Save and close") == "never"
        assert aa.classify_action_button("Cancel") == "never"
        assert aa.classify_action_button("Back") == "never"


class TestFindApplicationTab:
    class _Page:
        def __init__(self, url):
            self.url = url

    class _Ctx:
        def __init__(self, pages):
            self.pages = pages

    def test_returns_apply_surface_tab(self):
        ctx = self._Ctx([
            self._Page("https://www.indeed.com/viewjob?jk=abc"),
            self._Page("https://smartapply.indeed.com/beta/indeedapply/form/resume"),
        ])
        page = aa.find_application_tab(ctx, "https://www.indeed.com/viewjob?jk=abc")
        assert page.url.startswith("https://smartapply.indeed.com")

    def test_returns_job_url_match(self):
        ctx = self._Ctx([self._Page("https://www.indeed.com/viewjob?jk=abc123")])
        page = aa.find_application_tab(ctx, "https://www.indeed.com/viewjob?jk=abc123")
        assert page is not None

    def test_prefers_smartapply_over_job_and_blank_tabs(self):
        ctx = self._Ctx([
            self._Page("https://smartapply.indeed.com/beta/indeedapply/form/resume"),
            self._Page("https://www.indeed.com/viewjob?jk=abc123"),
            self._Page("about:blank"),
        ])
        page = aa.find_application_tab(ctx, "https://www.indeed.com/viewjob?jk=abc123")
        assert page.url.startswith("https://smartapply.indeed.com")

    def test_none_when_no_match(self):
        ctx = self._Ctx([self._Page("https://example.com"), self._Page("about:blank")])
        assert aa.find_application_tab(ctx, "https://www.indeed.com/viewjob?jk=zzz") is None


class TestMultiStepApply:
    def test_advances_uploads_resume_and_stops_before_submit(self, tmp_path):
        pytest.importorskip("playwright.sync_api")
        resume = tmp_path / "resume.pdf"
        resume.write_bytes(b"%PDF-1.4 fake resume\n")
        profile = dict(PROFILE, resume_path=str(resume))

        fixture = Path(__file__).parent / "fixtures" / "fake_multistep_apply.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            profile,
            headless=True,
            timeout_ms=15_000,
        )
        page = report["_page"]
        try:
            # Walked step 1 -> step 2 -> step 3, then stopped at the submit step.
            assert report.get("steps_completed") == 2
            assert "stopped_before_submit" in report["stages"]
            assert report["submitted"] is False
            # Step 1 contact fields were filled (still in the DOM though hidden).
            assert page.locator("#first_name").input_value() == "Alex"
            assert page.locator("#email").input_value() == "alex@example.com"
            # Resume was uploaded on step 2.
            assert any("uploaded" in item.lower() for item in report["filled"])
            assert report["resume_uploaded"] is True
            assert report["stopped_reason"] == "stopped_before_submit"
            assert report["current_action"] == "Stopped before submit"
            # The final submit button was never clicked.
            assert page.locator("#submitted").input_value() == "no"
        finally:
            aa.close_report_browser(report)

    def test_missing_resume_path_stops_on_resume_step(self):
        pytest.importorskip("playwright.sync_api")
        fixture = Path(__file__).parent / "fixtures" / "fake_multistep_apply.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            PROFILE,
            headless=True,
            timeout_ms=15_000,
        )
        page = report["_page"]
        try:
            assert report.get("steps_completed") == 1
            assert report["stopped_reason"] == "resume_needs_review"
            assert any("resume_path" in item for item in report["current_step_needs_review"])
            assert page.locator("#step2").evaluate("(el) => el.classList.contains('active')")
            assert page.locator("#submitted").input_value() == "no"
        finally:
            aa.close_report_browser(report)

    def test_sensitive_field_stops_before_next(self):
        pytest.importorskip("playwright.sync_api")
        fixture = Path(__file__).parent / "fixtures" / "fake_multistep_sensitive.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            PROFILE,
            headless=True,
            timeout_ms=15_000,
        )
        page = report["_page"]
        try:
            assert report.get("steps_completed") == 0
            assert report["stopped_reason"] == "needs_review"
            assert any("Social Security" in item for item in report["current_step_needs_review"])
            assert page.locator("#step1").evaluate("(el) => el.classList.contains('active')")
            assert page.locator("#step2").evaluate("(el) => !el.classList.contains('active')")
        finally:
            aa.close_report_browser(report)

    def test_role_button_next_advances_and_stops_before_submit(self):
        pytest.importorskip("playwright.sync_api")
        fixture = Path(__file__).parent / "fixtures" / "fake_role_button_steps.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            PROFILE,
            headless=True,
            timeout_ms=15_000,
        )
        page = report["_page"]
        try:
            assert report.get("steps_completed") == 1
            assert report["advanced_steps"] == ["Next"]
            assert report["stopped_reason"] == "stopped_before_submit"
            assert page.locator("#submitted").input_value() == "no"
        finally:
            aa.close_report_browser(report)

    def test_verification_page_is_hard_stop(self):
        pytest.importorskip("playwright.sync_api")
        fixture = Path(__file__).parent / "fixtures" / "fake_verification_required.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            PROFILE,
            headless=True,
            timeout_ms=15_000,
        )
        try:
            assert "verification_required" in report["stages"]
            assert report["stopped_reason"] == "verification_required"
            assert report["filled_count"] == 0
        finally:
            aa.close_report_browser(report)

    def test_background_recaptcha_does_not_block_resume_upload(self, tmp_path):
        pytest.importorskip("playwright.sync_api")
        resume = tmp_path / "resume.pdf"
        resume.write_bytes(b"%PDF-1.4 fake resume\n")
        fixture = Path(__file__).parent / "fixtures" / "fake_recaptcha_resume.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(),
            {**PROFILE, "resume_path": str(resume)},
            headless=True,
            timeout_ms=15_000,
        )
        page = report["_page"]
        try:
            assert "background_verification_detected" in report["stages"]
            assert "verification_required" not in report["stages"]
            assert report["resume_uploaded"] is True
            assert any("uploaded" in item.lower() for item in report["filled"])
            assert page.locator("#submitted").input_value() == "no"
        finally:
            aa.close_report_browser(report)

    def test_resume_path_issue_names_missing_file(self):
        issue = aa.resume_path_issue({"resume_path": "/tmp/does-not-exist/resume.pdf"})
        assert issue == "Resume file missing: /tmp/does-not-exist/resume.pdf"


class TestRealChromeLauncher:
    def test_chrome_session_metadata_round_trips(self, tmp_path):
        profile_dir = tmp_path / "indeed"

        saved = aa.save_chrome_session(profile_dir, 9444)
        loaded = aa.load_chrome_session(profile_dir)

        assert saved["debug_port"] == 9444
        assert loaded is not None
        assert loaded["debug_port"] == 9444
        assert loaded["profile_path"] == str(profile_dir)

    def test_recovers_live_default_port_session(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "indeed"
        monkeypatch.setattr(aa, "chrome_debug_ready", lambda port: port == 9222)
        monkeypatch.setattr(aa, "chrome_process_uses_profile", lambda port, path: port == 9222 and path == profile_dir)

        recovered = aa.recover_chrome_session(profile_dir)

        assert recovered is not None
        assert recovered["debug_port"] == 9222
        assert recovered["recovered"] is True
        assert aa.load_chrome_session(profile_dir)["debug_port"] == 9222

    def test_visible_autofill_without_login_session_stops_before_browser_launch(self, monkeypatch):
        def fail_start_playwright():
            raise AssertionError("Playwright should not start without a CDP login session")

        monkeypatch.setattr(aa, "start_playwright", fail_start_playwright)

        report = aa.autofill_application(
            "https://www.indeed.com/viewjob?jk=abc",
            PROFILE,
            headless=False,
            cdp_port=None,
        )

        assert report["stopped_reason"] == "open_login_first"
        assert "Open Indeed Login first" in report["error"]
        assert "open_login_first" in report["stages"]

    def test_about_blank_after_cdp_navigation_reports_error(self, monkeypatch):
        class FakePage:
            url = "about:blank"

            def goto(self, *_args, **_kwargs):
                return None

            def title(self):
                return ""

        class FakeContext:
            pages = []

            def new_page(self):
                return FakePage()

        monkeypatch.setattr(aa, "start_playwright", lambda: object())
        monkeypatch.setattr(aa, "connect_user_chrome", lambda _pw, _port: (object(), FakeContext()))

        report = aa.autofill_application(
            "https://www.indeed.com/viewjob?jk=abc",
            PROFILE,
            headless=False,
            cdp_port=9444,
        )

        assert report["stopped_reason"] == "navigation_error"
        assert "about:blank" in report["navigation_error"]
        assert report["filled_count"] == 0

    def test_cdp_endpoint_url(self):
        assert aa.cdp_endpoint(9222) == "http://127.0.0.1:9222"

    def test_find_chrome_executable_returns_existing_macos_path(self, monkeypatch):
        macos_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        monkeypatch.setattr(aa.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(Path, "exists", lambda self: str(self) == macos_path)
        assert aa.find_chrome_executable() == macos_path

    def test_find_chrome_executable_none_when_absent(self, monkeypatch):
        monkeypatch.setattr(aa.platform, "system", lambda: "Linux")
        monkeypatch.setattr(Path, "exists", lambda self: False)
        monkeypatch.setattr(aa.shutil, "which", lambda name: None)
        assert aa.find_chrome_executable() is None

    def test_launch_user_chrome_builds_plain_args(self, monkeypatch, tmp_path):
        captured = {}

        class FakeProc:
            def poll(self):
                return None

        def fake_popen(args, **kwargs):
            captured["args"] = args
            return FakeProc()

        monkeypatch.setattr(aa, "find_chrome_executable", lambda: "/fake/chrome")
        monkeypatch.setattr(aa.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(aa, "chrome_debug_ready", lambda port: True)

        proc = aa.launch_user_chrome(tmp_path / "profile", 9222, start_url="https://x")
        assert isinstance(proc, FakeProc)
        args = captured["args"]
        assert args[0] == "/fake/chrome"
        assert "--remote-debugging-port=9222" in args
        assert any(a.startswith("--user-data-dir=") for a in args)
        assert args[-1] == "https://x"
        # Keep Chrome launch simple; verification/login challenges remain manual.
        assert not any("enable-automation" in a for a in args)

    def test_launch_user_chrome_requires_chrome(self, monkeypatch, tmp_path):
        monkeypatch.setattr(aa, "find_chrome_executable", lambda: None)
        with pytest.raises(RuntimeError):
            aa.launch_user_chrome(tmp_path / "profile", 9222, chrome_path=None)

    def test_never_submits_constant_buttons(self):
        # Guardrail: submit-like controls are recognized so they are left alone.
        assert aa.is_submit_like("Submit application")
        assert aa.is_submit_like("Continue")
        assert not aa.is_submit_like("First name")


class TestCleanPromptLabel:
    def test_strips_hash_framework_id_and_required_marker(self):
        raw = ("q_c05e0398d79935ef9e3661321d291e28 rich-text-question-input-:r2c: "
               "What are 3 things you'd look for in an ideal job? *")
        assert aa.clean_prompt_label(raw) == "What are 3 things you'd look for in an ideal job?"

    def test_strips_multiselect_option_prefix(self):
        raw = "multi-select-question-:rn:-0 3 3 How many shifts per week are you looking to work?"
        assert aa.clean_prompt_label(raw) == "How many shifts per week are you looking to work?"

    def test_pure_hash_becomes_empty(self):
        assert aa.clean_prompt_label("q_93ba5d63afd79925ba52cb7dd81a1791") == ""

    def test_preserves_real_words(self):
        assert aa.clean_prompt_label("What field do you want to work in?") == "What field do you want to work in?"
        assert aa.clean_prompt_label("Email address") == "Email address"


class TestDedupeReviewItems:
    def test_collapses_repeated_multiselect_question(self):
        items = [
            "multi-select-question-:rn:-0 3 3 How many shifts per week? (needs review: no matching option)",
            "multi-select-question-:rn:-1 4 4 How many shifts per week? (needs review: no matching option)",
            "multi-select-question-:rn:-2 5 5 How many shifts per week? (needs review: no matching option)",
        ]
        assert len(aa.dedupe_review_items(items)) == 1

    def test_keeps_distinct_items(self):
        items = [
            "What are 3 things you'd look for? (needs review: no confident answer)",
            "Reached the final submit step — review and submit it yourself.",
        ]
        assert len(aa.dedupe_review_items(items)) == 2


class TestMultiSelectQuestionLabel:
    def test_copies_group_question_once_not_each_option(self):
        pytest.importorskip("playwright.sync_api")
        fixture = Path(__file__).parent / "fixtures" / "fake_multiselect_question.html"
        report = aa.autofill_application(
            fixture.resolve().as_uri(), PROFILE, headless=True, timeout_ms=15_000
        )
        try:
            shift_items = [r for r in report["needs_review"] if "shift" in r.lower()]
            # Exactly one review item, and it is the question (not "3"/"4"/"5").
            assert len(shift_items) == 1
            assert "How many shifts per week" in shift_items[0]
            # No bare option numbers leaked in as their own review items.
            for r in report["needs_review"]:
                head = r.strip()[:2]
                assert head not in ("3 ", "4 ", "5 ")
                assert r.strip() not in ("3", "4", "5")
        finally:
            aa.close_report_browser(report)
