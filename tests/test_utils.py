import utils


class TestCleanText:
    def test_collapses_whitespace_and_newlines(self):
        assert utils.clean_text("  a\n b\t c ") == "a b c"

    def test_replaces_non_breaking_space(self):
        assert utils.clean_text("a\xa0b") == "a b"

    def test_none_and_empty_become_not_specified(self):
        assert utils.clean_text(None) == utils.NOT_SPECIFIED
        assert utils.clean_text("") == utils.NOT_SPECIFIED
        assert utils.clean_text("   ") == utils.NOT_SPECIFIED

    def test_coerces_non_strings(self):
        assert utils.clean_text(42) == "42"


class TestParseFloat:
    def test_parses_numeric_strings_and_numbers(self):
        assert utils.parse_float("18.5") == 18.5
        assert utils.parse_float(7) == 7.0

    def test_returns_none_on_bad_input(self):
        assert utils.parse_float("n/a") is None
        assert utils.parse_float(None) is None


class TestSlugifyLocation:
    def test_slugifies_city_state(self):
        assert utils.slugify_location("Cupertino, CA") == "cupertino_ca"

    def test_strips_leading_trailing_separators(self):
        assert utils.slugify_location("  San Jose!  ") == "san_jose"

    def test_empty_falls_back_to_location(self):
        assert utils.slugify_location("!!!") == "location"
