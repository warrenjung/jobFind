"""Unit tests for the pure helpers in local_search_server.py."""

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
