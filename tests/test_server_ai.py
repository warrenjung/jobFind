import requests

import server_ai as sai


class TestExtractText:
    def test_openai_prefers_output_text(self):
        assert sai.extract_openai_text({"output_text": "  hello  "}) == "hello"

    def test_openai_falls_back_to_content_chunks(self):
        payload = {
            "output": [
                {"content": [{"text": "part one"}, {"text": "part two"}]},
            ]
        }
        assert sai.extract_openai_text(payload) == "part one\npart two"

    def test_openai_empty_payload(self):
        assert sai.extract_openai_text({}) == ""

    def test_ollama_extract(self):
        assert sai.extract_ollama_text({"response": "  hi there  "}) == "hi there"
        assert sai.extract_ollama_text({}) == ""


class TestProviderStatus:
    def test_openai_when_key_present(self):
        status = sai.ai_provider_status("sk-x", "gpt-x", False, "llama", "no provider")
        assert status["enabled"] is True
        assert status["provider"] == "openai"
        assert status["model"] == "gpt-x"

    def test_ollama_when_no_key_but_available(self):
        status = sai.ai_provider_status(None, "gpt-x", True, "llama3", "no provider")
        assert status["enabled"] is True
        assert status["provider"] == "ollama"
        assert status["model"] == "llama3"

    def test_disabled_when_nothing_available(self):
        status = sai.ai_provider_status(None, "gpt-x", False, "llama3", "set OPENAI_API_KEY")
        assert status["enabled"] is False
        assert status["provider"] == "none"
        assert status["message"] == "set OPENAI_API_KEY"


class TestOllamaAvailable:
    def test_returns_false_on_connection_error(self):
        class Boom:
            def get(self, *a, **k):
                raise requests.RequestException("no server")

        assert sai.ollama_available("http://localhost:11434", requests_mod=Boom()) is False

    def test_returns_true_on_ok(self):
        class Ok:
            def get(self, *a, **k):
                class R:
                    status_code = 200
                return R()

        assert sai.ollama_available("http://localhost:11434", requests_mod=Ok()) is True
