import server_profile as sp


def _clean(value, max_length=5000):
    text = "" if value is None else str(value).strip()
    return text[:max_length]


class TestCommonAnswerFieldMap:
    def test_field_map_has_expected_shape(self):
        field_map = sp.common_answer_field_map()
        assert field_map
        for key, meta in field_map.items():
            assert meta["key"] == key
            assert meta["label"]
            assert meta["prompt"]


class TestCommonAnswersFromProfile:
    def test_reads_answers_via_prompt(self):
        field_map = sp.common_answer_field_map()
        sample_key = next(iter(field_map))
        prompt = field_map[sample_key]["prompt"]
        profile = {"custom_answers": {prompt: "My stored answer"}}
        result = sp.common_answers_from_profile(profile, field_map, {}, _clean)
        assert result[sample_key] == "My stored answer"

    def test_missing_answers_are_blank(self):
        field_map = sp.common_answer_field_map()
        result = sp.common_answers_from_profile({}, field_map, {}, _clean)
        assert all(value == "" for value in result.values())


class TestCommonAnswersPayload:
    def test_rejects_non_object(self):
        field_map = sp.common_answer_field_map()
        result, error = sp.common_answers_payload({"answers": "nope"}, field_map, _clean)
        assert result == {}
        assert error

    def test_keeps_only_known_keys(self):
        field_map = sp.common_answer_field_map()
        sample_key = next(iter(field_map))
        payload = {"answers": {sample_key: "value", "unknown_key": "x"}}
        result, error = sp.common_answers_payload(payload, field_map, _clean)
        assert error is None
        assert result == {sample_key: "value"}
