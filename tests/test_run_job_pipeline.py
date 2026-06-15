"""Unit tests for run_job_pipeline.py command wiring."""

import sys

import run_job_pipeline as rjp


class TestPersonalKeywords:
    def test_main_passes_personal_keywords_to_ranker(self, tmp_path, monkeypatch):
        indeed_file = tmp_path / "indeed_jobs.csv"
        indeed_file.write_text("title,company,job_url\n", encoding="utf-8")
        commands = []

        monkeypatch.setattr(rjp, "run_command", commands.append)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_job_pipeline.py",
                "--location",
                "Cupertino, CA",
                "--skip-indeed",
                "--indeed-file",
                str(indeed_file),
                "--skip-clean-table",
                "--personal-keywords",
                "barista, tutoring",
            ],
        )

        rjp.main()

        rank_command = commands[0]
        index = rank_command.index("--personal-keywords")
        assert rank_command[index + 1] == "barista, tutoring"

    def test_main_passes_personal_keywords_as_extra_scrape_queries(self, tmp_path, monkeypatch):
        commands = []
        monkeypatch.setattr(rjp, "run_command", commands.append)
        monkeypatch.setattr(rjp, "ensure_indeed_csv", lambda *a, **k: tmp_path / "indeed.csv")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_job_pipeline.py",
                "--location",
                "Cupertino, CA",
                "--skip-clean-table",
                "--personal-keywords",
                "tutoring, summer camp",
            ],
        )

        rjp.main()

        scrape_command = commands[0]
        index = scrape_command.index("--extra-queries")
        assert scrape_command[index + 1:index + 3] == ["tutoring", "summer camp"]


class TestScrapeIndeedExtraQueries:
    def test_extra_queries_added_to_command(self, monkeypatch):
        commands = []
        monkeypatch.setattr(rjp, "run_command", commands.append)
        rjp.scrape_indeed(
            "Cupertino, CA", "cupertino_ca", 10, 1, None, 14, extra_queries=["tutoring", "camp"]
        )
        cmd = commands[0]
        index = cmd.index("--extra-queries")
        assert cmd[index + 1:index + 3] == ["tutoring", "camp"]

    def test_no_extra_queries_flag_when_empty(self, monkeypatch):
        commands = []
        monkeypatch.setattr(rjp, "run_command", commands.append)
        rjp.scrape_indeed("Cupertino, CA", "cupertino_ca", 10, 1, None, 14, extra_queries=[])
        assert "--extra-queries" not in commands[0]
