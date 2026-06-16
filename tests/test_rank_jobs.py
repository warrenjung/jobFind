"""Unit tests for the scoring/ranking helpers in rank_jobs.py."""

import rank_jobs as rj


class TestParseHourlyPay:
    def test_single_hourly(self):
        assert rj.parse_hourly_pay("$18.70 an hour") == 18.70

    def test_range_is_midpoint(self):
        assert rj.parse_hourly_pay("$20 - $24 an hour") == 22.0

    def test_non_hourly_returns_none(self):
        assert rj.parse_hourly_pay("$95,000 a year") is None

    def test_unspecified_returns_none(self):
        assert rj.parse_hourly_pay("Not specified") is None

    def test_ignores_out_of_range_numbers(self):
        # "must be 18+" style numbers below the wage floor are ignored.
        assert rj.parse_hourly_pay("$19 an hour, must be 1 year old") == 19.0


class TestExtractCityState:
    def test_city(self):
        assert rj.extract_city("Cupertino, CA 95014") == "Cupertino"

    def test_state(self):
        assert rj.extract_state("Cupertino, CA 95014") == "CA"

    def test_state_missing(self):
        assert rj.extract_state("Remote") == rj.NOT_SPECIFIED


class TestHaversine:
    def test_zero_distance(self):
        assert rj.haversine_miles((37.0, -122.0), (37.0, -122.0)) == 0.0

    def test_known_distance_sf_to_la(self):
        miles = rj.haversine_miles((37.7749, -122.4194), (34.0522, -118.2437))
        assert 330 < miles < 360  # ~347 miles


class TestScoreSourceFit:
    def test_indeed_bonus(self):
        score, _ = rj.score_source_fit({"source": "indeed"})
        assert score == 4

    def test_usajobs_penalty_without_student_wording(self):
        score, reasons = rj.score_source_fit(
            {"source": "usajobs", "title": "Program Analyst", "description": "Federal role"}
        )
        assert score == -12

    def test_careeronestop_bonus(self):
        score, _ = rj.score_source_fit({"source": "careeronestop"})
        assert score == 2

    def test_ats_bonus(self):
        score, reasons = rj.score_source_fit({"source": "ats_greenhouse"})
        assert score == 3
        assert reasons == ["+3 direct company career page source"]


class TestPersonalKeywordScoring:
    JOB = {
        "title": "Summer Camp Tutor",
        "company": "Example Learning",
        "location": "Cupertino, CA",
        "job_type": "Part-time",
        "schedule": "Flexible",
        "description": "Help students with reading activities.",
        "teen_fit_reason": "Part-time friendly",
    }

    def test_parse_personal_keywords_trims_dedupes_and_lowercases(self):
        assert rj.parse_personal_keywords(" Barista, tutoring, barista ") == ["barista", "tutoring"]

    def test_blank_personal_keywords_no_score(self):
        score, reasons, matched = rj.score_personal_keywords(self.JOB, [])

        assert score == 0
        assert reasons == []
        assert matched == []

    def test_personal_keyword_match_scores_phrase_case_insensitive(self):
        score, reasons, matched = rj.score_personal_keywords(self.JOB, ["summer camp"])

        assert score == rj.PERSONAL_KEYWORD_POINTS
        assert reasons == ["+8 personal keyword match: summer camp"]
        assert matched == ["summer camp"]

    def test_personal_keyword_score_is_capped(self):
        score, reasons, matched = rj.score_personal_keywords(
            self.JOB,
            ["summer", "camp", "tutor", "students"],
        )

        assert score == rj.PERSONAL_KEYWORD_CAP
        assert f"score capped at +{rj.PERSONAL_KEYWORD_CAP} for personal keyword matches" in reasons
        assert matched == ["summer", "camp", "tutor", "students"]

    def test_rate_job_includes_personal_keyword_reason(self):
        rated = rj.rate_job(self.JOB, ["tutor"])

        assert any("personal keyword match: tutor" in reason for reason in rated["rating_reasons"])
        assert rated["matched_personal_keywords"] == ["tutor"]

    def test_rate_job_no_keywords_has_empty_matches(self):
        assert rj.rate_job(self.JOB, [])["matched_personal_keywords"] == []

    def test_blank_avoid_keywords_no_score(self):
        score, reasons, matched = rj.score_avoid_keywords(self.JOB, [])

        assert score == 0
        assert reasons == []
        assert matched == []

    def test_avoid_keyword_match_penalizes_phrase_case_insensitive(self):
        score, reasons, matched = rj.score_avoid_keywords(
            {**self.JOB, "description": "Overnight tutoring shift."},
            ["overnight"],
        )

        assert score == rj.AVOID_KEYWORD_POINTS
        assert reasons == ["-10 avoid keyword match: overnight"]
        assert matched == ["overnight"]

    def test_avoid_keyword_score_is_capped(self):
        score, reasons, matched = rj.score_avoid_keywords(
            {**self.JOB, "description": "Manager overnight full-time medical driver role."},
            ["manager", "overnight", "full-time", "medical", "driver"],
        )

        assert score == rj.AVOID_KEYWORD_CAP
        assert f"score capped at {rj.AVOID_KEYWORD_CAP} for avoid keyword matches" in reasons
        assert matched == ["manager", "overnight", "full-time", "medical", "driver"]

    def test_rate_job_includes_avoid_keyword_reason(self):
        rated = rj.rate_job({**self.JOB, "description": "Overnight shift."}, [], ["overnight"])

        assert any("avoid keyword match: overnight" in reason for reason in rated["rating_reasons"])
        assert rated["matched_avoid_keywords"] == ["overnight"]

    def test_preferred_and_avoid_keywords_can_both_match(self):
        rated = rj.rate_job(
            {**self.JOB, "description": "Tutor opening with full-time schedule."},
            ["tutor"],
            ["full-time"],
        )

        assert rated["matched_personal_keywords"] == ["tutor"]
        assert rated["matched_avoid_keywords"] == ["full-time"]

    def test_rank_jobs_accepts_personal_keywords(self):
        jobs = [
            {**self.JOB, "title": "Tutor", "url": "https://example.com/tutor"},
            {**self.JOB, "title": "Cashier", "description": "Run register.", "url": "https://example.com/cashier"},
        ]

        ranked = rj.rank_jobs(jobs, "Cupertino, CA", ["tutor"])

        assert ranked[0]["title"] == "Tutor"
        assert any("personal keyword match: tutor" in reason for reason in ranked[0]["rating_reasons"])

    def test_rank_jobs_accepts_avoid_keywords(self):
        jobs = [
            {**self.JOB, "title": "Tutor", "description": "Overnight tutoring shift.", "url": "https://example.com/tutor"},
        ]

        ranked = rj.rank_jobs(jobs, "Cupertino, CA", [], ["overnight"])

        assert ranked[0]["matched_avoid_keywords"] == ["overnight"]
        assert any("avoid keyword match: overnight" in reason for reason in ranked[0]["rating_reasons"])


class TestDedupeJobs:
    def test_dedupes_by_url(self):
        jobs = [
            {"source": "indeed", "url": "https://x/viewjob?jk=1", "title": "Cashier"},
            {"source": "indeed", "url": "https://x/viewjob?jk=1", "title": "Cashier"},
        ]
        assert len(rj.dedupe_jobs(jobs)) == 1

    def test_keeps_distinct(self):
        jobs = [
            {"source": "indeed", "url": "https://x/viewjob?jk=1", "title": "Cashier"},
            {"source": "indeed", "url": "https://x/viewjob?jk=2", "title": "Barista"},
        ]
        assert len(rj.dedupe_jobs(jobs)) == 2


class TestLabelScore:
    def test_buckets(self):
        assert rj.label_score(90) == "Great student fit"
        assert rj.label_score(70) == "Good student fit"
        assert rj.label_score(50) == "Possible fit"
        assert rj.label_score(30) == "Weak fit"
        assert rj.label_score(10) == "Poor fit"


class TestLoadUsajobsOptional:
    def test_missing_file_returns_empty(self):
        assert rj.load_usajobs("") == []
        assert rj.load_usajobs("/nonexistent/path/jobs_raw.json") == []


class TestLoadAtsJobs:
    def test_blank_file_returns_empty(self):
        assert rj.load_ats_jobs("") == []

    def test_normalizes_ats_rows(self, tmp_path):
        path = tmp_path / "jobs_ats.json"
        path.write_text(
            """[
              {
                "source": "ats_lever",
                "source_id": "lever:acme:1",
                "title": "Barista",
                "company": "Acme Cafe",
                "location": "Cupertino, CA",
                "pay": "$18-$22 an hour",
                "job_type": "Part-time",
                "description": "Customer service role.",
                "url": "https://jobs.lever.co/acme/1"
              }
            ]""",
            encoding="utf-8",
        )

        jobs = rj.load_ats_jobs(str(path))

        assert jobs[0]["source"] == "ats_lever"
        assert jobs[0]["city"] == "Cupertino"
        assert jobs[0]["state"] == "CA"
        assert jobs[0]["hourly_pay_estimate"] == 20.0
