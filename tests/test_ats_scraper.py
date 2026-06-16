"""Tests for optional Greenhouse/Lever ATS source normalization."""

import json

import pytest

import ats_scraper as ats


class TestHtmlCleanup:
    def test_strip_html_returns_readable_text(self):
        assert ats.strip_html("<p>Hello<br>students</p>") == "Hello students"

    def test_strip_html_handles_missing(self):
        assert ats.strip_html(None) == ats.NOT_SPECIFIED


class TestGreenhouseNormalization:
    def test_normalizes_greenhouse_job(self):
        row = {
            "id": 123,
            "title": "Team Member",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
            "updated_at": "2026-06-01T12:00:00Z",
            "content": "<p>Part-time cashier role.</p>",
            "location": {"name": "Cupertino, CA"},
            "departments": [{"name": "Stores"}],
        }

        job = ats.normalize_greenhouse_job(row, board_token="acme", company="Acme")

        assert job["source"] == "ats_greenhouse"
        assert job["source_id"] == "greenhouse:acme:123"
        assert job["title"] == "Team Member"
        assert job["company"] == "Acme"
        assert job["department"] == "Stores"
        assert job["location"] == "Cupertino, CA"
        assert job["city"] == "Cupertino"
        assert job["state"] == "CA"
        assert job["description"] == "Part-time cashier role."
        assert job["url"] == "https://boards.greenhouse.io/acme/jobs/123"

    def test_greenhouse_location_falls_back_to_offices(self):
        row = {"offices": [{"name": "San Jose, CA"}, {"name": "San Jose, CA"}]}
        assert ats.greenhouse_location(row) == "San Jose, CA"


class TestLeverNormalization:
    def test_normalizes_lever_job(self):
        row = {
            "id": "abc",
            "text": "Retail Associate",
            "hostedUrl": "https://jobs.lever.co/acme/abc",
            "createdAt": 1780000000000,
            "descriptionPlain": "Help customers on weekends.",
            "categories": {
                "team": "Retail",
                "location": "San Jose, CA",
                "commitment": "Part-time",
            },
            "salaryRange": {"description": "$18-$22 an hour"},
        }

        job = ats.normalize_lever_job(row, site="acme", company="Acme")

        assert job["source"] == "ats_lever"
        assert job["source_id"] == "lever:acme:abc"
        assert job["title"] == "Retail Associate"
        assert job["company"] == "Acme"
        assert job["department"] == "Retail"
        assert job["location"] == "San Jose, CA"
        assert job["job_type"] == "Part-time"
        assert job["pay"] == "$18-$22 an hour"

    def test_lever_location_uses_all_locations_when_needed(self):
        assert ats.lever_location({"allLocations": ["Remote", "Cupertino, CA"]}) == "Remote, Cupertino, CA"


class TestConfig:
    def test_load_sources_accepts_sources_object(self, tmp_path):
        path = tmp_path / "ats_sources.json"
        path.write_text(
            json.dumps({"sources": [{"provider": "lever", "site": "acme"}]}),
            encoding="utf-8",
        )

        assert ats.load_sources(path) == [{"provider": "lever", "site": "acme"}]

    def test_load_sources_missing_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            ats.load_sources(tmp_path / "missing.json")


class TestLocationFilter:
    def test_matches_requested_city(self):
        assert ats.matches_location({"location": "Cupertino, CA"}, "Cupertino, CA")

    def test_does_not_match_state_only_for_city_state_query(self):
        assert not ats.matches_location(
            {"location": "Los Angeles, CA"}, "Cupertino, CA", include_remote=False
        )

    def test_matches_multi_word_city_phrase(self):
        assert ats.matches_location(
            {"location": "San Jose, CA"}, "San Jose, CA", include_remote=False
        )

    def test_matches_zip_code(self):
        assert ats.matches_location(
            {"location": "Cupertino, CA 95014"}, "95014", include_remote=False
        )

    def test_keeps_remote_when_allowed(self):
        assert ats.matches_location({"location": "Remote"}, "Cupertino, CA")

    def test_excludes_other_city(self):
        assert not ats.matches_location(
            {"location": "New York, NY"}, "Cupertino, CA", include_remote=False
        )
