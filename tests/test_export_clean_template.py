import export_clean_template as ect


class TestRenderPage:
    def test_renders_full_html_document(self):
        html = ect.render_page(
            header_title="Job Results",
            header_summary="3 jobs",
            cards_html="<article>card</article>",
            controls_html="<div class='controls'></div>",
            no_match_html="",
            script_html="<script>1</script>",
        )
        assert html.lstrip().startswith("<!doctype html>")
        assert "<article>card</article>" in html
        assert "Job Results" in html
        assert "3 jobs" in html
        assert html.rstrip().endswith("</html>")

    def test_escapes_header_title(self):
        html = ect.render_page(
            header_title="A & B <x>",
            header_summary="ok",
            cards_html="",
            controls_html="",
            no_match_html="",
            script_html="",
        )
        assert "A &amp; B &lt;x&gt;" in html


class TestBuildControls:
    def test_includes_source_options(self):
        controls = ect.build_controls_html("<option>Indeed</option>")
        assert "<option>Indeed</option>" in controls


class TestStatusStyling:
    def test_status_palette_has_distinct_classes(self):
        assert "--status-progress" in ect.PAGE_CSS
        assert "--status-applied" in ect.PAGE_CSS
        assert "--status-follow" in ect.PAGE_CSS
        assert "--status-skipped" in ect.PAGE_CSS
        assert ".application-status.in-progress" in ect.PAGE_CSS
        assert ".application-status.applied" in ect.PAGE_CSS
        assert ".application-status.follow-up" in ect.PAGE_CSS
        assert ".application-status.skipped" in ect.PAGE_CSS


class TestResultPublishing:
    def test_script_publishes_ranked_jobs_to_parent_dashboard(self):
        assert "collectResultJobs" in ect.PAGE_SCRIPT
        assert "jobfind:results-jobs" in ect.PAGE_SCRIPT
        assert "sourceLabel" in ect.PAGE_SCRIPT


class TestRankingExplanationStyles:
    def test_explanation_styles_are_present(self):
        assert ".ranking-explanation" in ect.PAGE_CSS
        assert ".reason-list li.positive" in ect.PAGE_CSS
        assert ".reason-list li.negative" in ect.PAGE_CSS
        assert ".keyword-chip.avoid" in ect.PAGE_CSS


class TestTimestamp:
    def test_timestamp_is_nonempty_string(self):
        assert isinstance(ect.generated_timestamp(), str)
        assert ect.generated_timestamp()
