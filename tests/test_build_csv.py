"""Unit tests for the teen-friendly filter in build_csv.py."""

import build_csv as bc


class TestExcludedReason:
    def test_keeps_plain_cashier(self):
        job = {"title": "Cashier", "description_snippet": "No experience required."}
        assert bc.excluded_reason(job) is None

    def test_excludes_degree(self):
        job = {"title": "Analyst", "description_snippet": "Bachelor's degree required."}
        assert bc.excluded_reason(job) is not None

    def test_excludes_license(self):
        job = {"title": "Caregiver", "description_snippet": "Must have RN license."}
        assert bc.excluded_reason(job) is not None

    def test_excludes_experience(self):
        job = {"title": "Lead", "description_snippet": "5+ years experience required."}
        assert bc.excluded_reason(job) is not None

    def test_excludes_management_title(self):
        job = {"title": "Store Manager", "description_snippet": "Run the store."}
        reason = bc.excluded_reason(job)
        assert reason is not None and "anagement" in reason

    def test_allows_drivers_license(self):
        # A plain driver's license is fine for an 18-year-old.
        job = {"title": "Delivery Driver", "description_snippet": "Valid driver's license required."}
        assert bc.excluded_reason(job) is None

    def test_empty_job_is_kept(self):
        assert bc.excluded_reason({}) is None


class TestAnnualSalaryTooHigh:
    def test_high_annual_flagged(self):
        assert bc.annual_salary_too_high("$95,000 - $120,000 a year") is not None

    def test_hourly_not_flagged(self):
        assert bc.annual_salary_too_high("$20 an hour") is None

    def test_low_annual_not_flagged(self):
        assert bc.annual_salary_too_high("$30,000 a year") is None


class TestDedupeKey:
    def test_prefers_url(self):
        job = {"job_url": "https://x/viewjob?jk=AB", "title": "T", "company": "C"}
        assert bc.dedupe_key(job) == "https://x/viewjob?jk=ab"

    def test_falls_back_to_title_company(self):
        job = {"job_url": "", "title": "Cashier", "company": "Target"}
        assert bc.dedupe_key(job) == "cashier|target"


class TestFitReason:
    def test_detects_positive_signals(self):
        job = {"title": "Cashier", "description_snippet": "No experience, part-time, will train."}
        reason = bc.fit_reason(job)
        assert "No experience required" in reason

    def test_default_when_no_signals(self):
        job = {"title": "Clerk", "description_snippet": "Help customers."}
        assert bc.fit_reason(job) == "No degree or certification mentioned"
