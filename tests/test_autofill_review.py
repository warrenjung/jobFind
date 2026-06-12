import autofill_review as ar


class TestPromptLabels:
    def test_clean_prompt_label_strips_machine_ids(self):
        assert ar.clean_prompt_label("Why do you want this job?") == "Why do you want this job?"

    def test_clean_prompt_label_blank_for_not_specified(self):
        assert ar.clean_prompt_label(None) == ""

    def test_clean_prompt_label_drops_leading_numbers(self):
        # leading "1 2 " enumeration noise should be removed
        assert ar.clean_prompt_label("1 2 Tell us about yourself") == "Tell us about yourself"

    def test_has_yes_no_options(self):
        assert ar.has_yes_no_options(["Yes", "No"]) is True
        assert ar.has_yes_no_options(["3", "4", "5"]) is False
        assert ar.has_yes_no_options([]) is False

    def test_fallback_review_question_uses_clean_label(self):
        assert ar.fallback_review_question("text", "Preferred start date") == "Preferred start date"

    def test_fallback_review_question_yes_no(self):
        assert (
            ar.fallback_review_question("radio", "q_abcdef012345", ["Yes", "No"])
            == "Yes/No question on this step"
        )

    def test_fallback_review_question_generic(self):
        assert ar.fallback_review_question("text", "") == "Review this field on the page"


class TestReasonAndLegacyText:
    def test_review_reason_text_aliases(self):
        assert ar.review_reason_text("needs review: sensitive question") == "Sensitive question"

    def test_review_reason_text_defaults(self):
        assert ar.review_reason_text("") == "Needs review"

    def test_legacy_text_combines_question_and_detail(self):
        item = {"question": "Why here?", "reason_detail": "no confident answer"}
        assert ar.review_item_legacy_text(item) == "Why here? (no confident answer)"

    def test_legacy_text_falls_back_to_detail(self):
        item = {"question": "", "reason_detail": "sensitive question"}
        assert ar.review_item_legacy_text(item) == "sensitive question"


class TestSuggestable:
    def test_resume_items_not_suggestable(self):
        item = {"kind": "resume", "question": "Upload resume", "reason": "resume step"}
        assert ar.is_suggestable_review_item(item) is False

    def test_sensitive_items_not_suggestable(self):
        item = {"kind": "text", "question": "What is your social security number?", "reason": "x"}
        assert ar.is_suggestable_review_item(item) is False

    def test_plain_question_is_suggestable(self):
        item = {"kind": "text", "question": "Why do you want this role?", "reason": "no confident answer"}
        assert ar.is_suggestable_review_item(item) is True


class TestAnswerForPrompt:
    def test_saved_answer_wins(self):
        profile = {"_saved_answers": {"why do you want this job": "Because I love the team."}}
        answer, reason = ar.answer_for_prompt("Why do you want this job?", profile)
        assert answer == "Because I love the team."
        assert reason == "saved answer"

    def test_sensitive_prompt_skipped(self):
        answer, reason = ar.answer_for_prompt("What is your SSN?", {})
        assert answer is None
        assert "sensitive" in reason

    def test_unknown_prompt_needs_review(self):
        answer, reason = ar.answer_for_prompt("Quantum flux capacitance?", {})
        assert answer is None
        assert reason == "needs review: no confident answer"


class TestBuildReviewItem:
    def test_build_item_shape(self):
        item = ar.build_review_item(
            question="Why do you want this job?",
            fallback="",
            reason="needs review: no confident answer",
            kind="text",
        )
        assert item["question"] == "Why do you want this job?"
        assert item["reason"] == "No confident answer"
        assert item["kind"] == "text"
        assert item["suggestable"] is True
        assert item["legacy_text"]

    def test_dedupe_review_items(self):
        a = ar.build_review_item(question="Same?", fallback="", reason="r", kind="text")
        b = ar.build_review_item(question="Same?", fallback="", reason="r", kind="text")
        assert len(ar.dedupe_review_items([a, b])) == 1


class TestSplitName:
    def test_split_name_variants(self):
        assert ar.split_name("Ada Lovelace") == ("Ada", "Lovelace")
        assert ar.split_name("Cher") == ("Cher", "")
        assert ar.split_name("") == ("", "")
