"""Unit tests for export_clean_table.py."""

import export_clean_table as ect


class TestCleanAndTruncate:
    def test_clean_collapses_whitespace(self):
        assert ect.clean_text("  a\n b  ") == "a b"

    def test_none_is_unspecified(self):
        assert ect.clean_text(None) == ect.NOT_SPECIFIED

    def test_truncate_adds_ellipsis(self):
        text = "x" * 300
        out = ect.truncate(text, 50)
        assert len(out) == 50 and out.endswith("...")


class TestFormatters:
    def test_format_source(self):
        assert ect.format_source({"source": "indeed"}) == "Indeed"
        assert ect.format_source({"source": "usajobs"}) == "USAJOBS"
        assert ect.format_source({"source": "ats_greenhouse"}) == "Greenhouse"
        assert ect.format_source({"source": "ats_lever"}) == "Lever"
        assert ect.format_source({"source": "weird"}) == ect.NOT_SPECIFIED

    def test_format_location_prefers_city_state(self):
        assert ect.format_location({"city": "Cupertino", "state": "CA"}) == "Cupertino, CA"

    def test_format_type_schedule_joins(self):
        out = ect.format_type_schedule({"job_type": "Part-time", "schedule": "Weekends"})
        assert "Part-time" in out and "Weekends" in out

    def test_format_distance(self):
        assert ect.format_distance(5.234) == "5.2 miles"
        assert ect.format_distance(None) == ect.NOT_SPECIFIED


class TestSortAndFilter:
    JOBS = [
        {"student_fit_score": 40, "title": "Low"},
        {"student_fit_score": 90, "title": "High"},
        {"student_fit_score": 65, "title": "Mid"},
    ]

    def test_sorted_by_score_desc(self):
        out = ect.sorted_by_score(self.JOBS)
        assert [j["title"] for j in out] == ["High", "Mid", "Low"]

    def test_filtered_jobs_threshold(self):
        out = ect.filtered_jobs(self.JOBS, 50)
        assert {j["title"] for j in out} == {"High", "Mid"}

    def test_prepared_jobs_filter_sort_limit(self):
        out = ect.prepared_jobs(self.JOBS, limit=1, min_score=50)
        assert [j["title"] for j in out] == ["High"]


class TestSortableHelpers:
    def test_sortable_pay(self):
        assert ect.sortable_pay({"hourly_pay_estimate": 22.5}) == 22.5
        assert ect.sortable_pay({}) == 0.0

    def test_sortable_distance_unknown_sinks(self):
        assert ect.sortable_distance({"distance_miles": 3.2}) == 3.2
        assert ect.sortable_distance({}) >= 1_000_000.0

    def test_search_blob_lowercases(self):
        blob = ect.search_blob({"title": "Cashier", "company": "Target", "city": "Cupertino", "state": "CA"})
        assert "cashier" in blob and "target" in blob and "cupertino" in blob


class TestApplyLinks:
    def test_html_apply_link_valid(self):
        out = ect.format_html_apply_link("https://www.indeed.com/viewjob?jk=1")
        assert out.startswith("<a") and "Apply" in out
        assert 'target="_blank"' in out
        assert 'rel="noopener"' in out

    def test_html_apply_link_rejects_non_http(self):
        out = ect.format_html_apply_link("javascript:alert(1)")
        assert "missing" in out

    def test_markdown_apply_link_valid(self):
        assert ect.format_markdown_apply_link("https://x/y") == "[Apply](https://x/y)"

    def test_markdown_apply_link_rejects(self):
        assert ect.format_markdown_apply_link("not-a-url") == ect.NOT_SPECIFIED


class TestEscaping:
    def test_markdown_cell_escapes_pipe(self):
        assert ect.escape_markdown_table_cell("a|b") == "a\\|b"


class TestBuildHtml:
    def test_includes_controls_and_data_attrs(self):
        jobs = [
            {
                "source": "indeed",
                "title": "Cashier",
                "company": "Target",
                "city": "Cupertino",
                "state": "CA",
                "student_fit_score": 80,
                "hourly_pay_estimate": 20,
                "distance_miles": 1.0,
                "url": "https://x/viewjob?jk=1",
                "rating_label": "Great student fit",
                "rating_reasons": ["+20 base score", "-8 full-time only"],
            }
        ]
        out = ect.build_html_cards(jobs, None, 50, location="Cupertino, CA", fromage=14)
        assert "Jobs near Cupertino, CA" in out
        assert "Postings from the last 14 days" in out
        assert 'id="job-search"' in out
        assert 'id="application-status"' in out
        assert 'id="show-all-jobs"' in out
        assert "dataset.localStatusLoaded" in out
        assert "Show all jobs" in out
        assert 'data-url="https://x/viewjob?jk=1"' in out
        assert 'data-application-status="need-to-apply"' in out
        assert "Need to apply" in out
        assert "jobfind:apply-assistant" in out
        assert 'data-title="Cashier"' in out
        assert 'data-company="Target"' in out
        assert 'data-source-label="Indeed"' in out
        assert 'data-pay="20"' in out
        assert "data-score" in out
        assert "Why ranked here" in out
        assert "+20 base score" in out
        assert "-8 full-time only" in out
        assert 'class="positive"' in out
        assert 'class="negative"' in out

    def test_empty_shows_message_no_controls(self):
        out = ect.build_html_cards([], None, 50, location="Nowhere, ZZ")
        assert "No jobs matched" in out
        assert 'id="job-search"' not in out


class TestMatchedPersonalKeywords:
    def _job(self, matched, avoid=None):
        return {
            "student_fit_score": 80,
            "title": "Barista",
            "company": "Cafe",
            "matched_personal_keywords": matched,
            "matched_avoid_keywords": avoid or [],
            "rating_label": "Great student fit",
            "rating_reasons": ["+20 base score"],
        }

    def test_row_shown_when_keywords_matched(self):
        out = ect.build_html_cards([self._job(["barista", "tutoring"])], None, 50)
        assert "Matches your keywords" in out
        assert "barista, tutoring" in out

    def test_row_absent_when_no_match(self):
        out = ect.build_html_cards([self._job([])], None, 50)
        assert "Matches your keywords" not in out

    def test_avoid_row_shown_when_keywords_matched(self):
        out = ect.build_html_cards([self._job([], ["manager", "overnight"])], None, 50)
        assert "Avoid keyword matches" in out
        assert "manager, overnight" in out
        assert "Matched avoid keywords" in out
        assert "keyword-chip avoid" in out


class TestRankingExplanation:
    def test_reason_class(self):
        assert ect.reason_class("+8 good") == "positive"
        assert ect.reason_class("-10 penalty") == "negative"
        assert ect.reason_class("score capped") == "neutral"

    def test_ranking_explanation_escapes_reasons(self):
        out = ect.ranking_explanation_html(
            {
                "student_fit_score": 70,
                "rating_label": "Good",
                "rating_reasons": ["+8 <match>", "-10 avoid"],
            }
        )

        assert "Why ranked here" in out
        assert "+8 &lt;match&gt;" in out
        assert 'class="positive"' in out
        assert 'class="negative"' in out
