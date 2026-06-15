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
