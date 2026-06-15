"""Unit tests for the network-free helpers in scrape_indeed.py."""

import scrape_indeed as si


class TestBuildQueryList:
    def test_defaults_when_no_queries(self):
        assert si.build_query_list(None, None) == si.SEARCH_QUERIES

    def test_appends_extras_after_base(self):
        assert si.build_query_list(["cashier"], ["tutoring"]) == ["cashier", "tutoring"]

    def test_dedupes_case_insensitively(self):
        assert si.build_query_list(["Cashier"], ["cashier", "TUTORING", "tutoring"]) == [
            "Cashier",
            "TUTORING",
        ]

    def test_strips_and_skips_blanks(self):
        assert si.build_query_list(["host"], ["  barista  ", ""]) == ["host", "barista"]

    def test_extras_dedupe_against_defaults(self):
        # "barista" is already in SEARCH_QUERIES, so it should not be duplicated.
        assert si.build_query_list(None, ["barista"]) == si.SEARCH_QUERIES


class TestExtractPay:
    def test_hourly_range(self):
        assert si.extract_pay("$21 - $25 an hour") == "$21 - $25 an hour"

    def test_single_hourly(self):
        assert si.extract_pay("$18.70 an hour") == "$18.70 an hour"

    def test_prefixed(self):
        assert si.extract_pay("From $18.45 an hour") == "From $18.45 an hour"

    def test_yearly(self):
        assert si.extract_pay("$50,000 - $60,000 a year") == "$50,000 - $60,000 a year"

    def test_rejects_css_blob_even_with_dollar_token(self):
        # A CSS/JS blob with a stray "$18 an hour" must not be captured as pay.
        assert si.extract_pay(".mosaic-provider-jobcards{margin:0} $18 an hour") is None

    def test_rejects_plain_text(self):
        assert si.extract_pay("Part-time") is None
        assert si.extract_pay("") is None


class TestLooksLikeMarkup:
    def test_detects_braces_and_keywords(self):
        assert si.looks_like_markup("{display:flex}")
        assert si.looks_like_markup("a" * 81)  # too long
        assert si.looks_like_markup(".mosaic-provider")

    def test_plain_pay_is_not_markup(self):
        assert not si.looks_like_markup("$20 an hour")


class TestClassifyAttributes:
    def test_picks_pay_type_schedule_and_skips_markup(self):
        attrs = [
            ".mosaic-provider-jobcards{margin:0} $18 an hour",  # markup, skipped
            "$22.50 an hour",
            "Part-time",
            "Weekends",
        ]
        pay, job_type, schedule = si.classify_attributes(attrs)
        assert pay == "$22.50 an hour"
        assert job_type == "Part-time"
        assert "Weekends" in schedule

    def test_all_unspecified_when_empty(self):
        pay, job_type, schedule = si.classify_attributes([])
        assert pay == si.NOT_SPECIFIED
        assert job_type == si.NOT_SPECIFIED
        assert schedule == si.NOT_SPECIFIED


class TestMakeSearchUrl:
    def test_includes_fromage_when_positive(self):
        url = si.make_search_url("cashier", "Cupertino, CA", 10, 0, fromage=14)
        assert "q=cashier" in url
        assert "radius=10" in url
        assert "start=0" in url
        assert "fromage=14" in url

    def test_omits_fromage_when_zero(self):
        url = si.make_search_url("cashier", "Cupertino, CA", 10, 0, fromage=0)
        assert "fromage" not in url

    def test_encodes_location_and_spaces(self):
        url = si.make_search_url("retail associate", "San Jose, CA", 25, 10)
        assert "retail+associate" in url
        assert "San+Jose%2C+CA" in url


class TestFreshnessLabel:
    def test_positive(self):
        assert si.freshness_label(14) == "Within last 14 days"

    def test_zero_is_unspecified(self):
        assert si.freshness_label(0) == si.NOT_SPECIFIED


class TestDedupeKey:
    def test_prefers_url(self):
        assert si.dedupe_key("https://x/viewjob?jk=1", "T", "C", "L") == "https://x/viewjob?jk=1"

    def test_falls_back_to_fields(self):
        key = si.dedupe_key(si.NOT_SPECIFIED, "Cashier", "Target", "Cupertino")
        assert key == "cashier|target|cupertino"


class TestCleanText:
    def test_collapses_whitespace(self):
        assert si.clean_text("  a\n  b\t c ") == "a b c"

    def test_empty_is_unspecified(self):
        assert si.clean_text("") == si.NOT_SPECIFIED
        assert si.clean_text(None) == si.NOT_SPECIFIED
