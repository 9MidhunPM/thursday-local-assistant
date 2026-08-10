from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from assistant.config.loader import load_config
from assistant.llm.client import ChatMessage, OpenAICompatibleClient, PROVIDER_PRESETS, resolve_provider_settings
from assistant.security import check_request_auth, redact_secrets


class ProviderResolveTests(unittest.TestCase):
    def test_local_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            s = resolve_provider_settings(provider="local")
            self.assertEqual(s["provider"], "local")
            self.assertTrue(s["is_local"])
            self.assertIn("127.0.0.1", s["base_url"])

    def test_openai_preset(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test", "LLM_PROVIDER": "openai"},
            clear=True,
        ):
            s = resolve_provider_settings()
            self.assertEqual(s["provider"], "openai")
            self.assertEqual(s["api_key"], "sk-test")
            self.assertIn("openai.com", s["base_url"])
            self.assertFalse(s["is_local"])

    def test_openrouter_model_default(self) -> None:
        s = resolve_provider_settings(provider="openrouter", api_key="x")
        self.assertEqual(s["provider"], "openrouter")
        self.assertTrue(s["model"])

    def test_presets_exist(self) -> None:
        for name in ("local", "openai", "openrouter", "groq", "together", "deepseek", "mistral"):
            self.assertIn(name, PROVIDER_PRESETS)

    def test_gpt_5_uses_max_completion_tokens(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://api.openai.com/v1",
            model="gpt-5.6-luna",
            temperature=0.2,
            provider="openai",
            max_tokens=512,
            timeout_sec=30,
            response_format=None,
        )
        try:
            payload = client._build_payload(
                [ChatMessage(role="user", content="Hello")],
                [
                    {
                        "type": "function",
                        "function": {"name": "test", "parameters": {"type": "object"}},
                    }
                ],
                False,
            )
        finally:
            client.close()
        self.assertEqual(payload["max_completion_tokens"], 512)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("temperature", payload)
        self.assertEqual(payload["reasoning_effort"], "none")

    def test_openai_config_ignores_legacy_llama_endpoint(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config" / "config.json"
        with mock.patch.dict(
            os.environ,
            {"LLM_PROVIDER": "openai", "LLAMA_HOST": "127.0.0.1", "LLAMA_PORT": "8080"},
            clear=True,
        ):
            config = load_config(config_path)
        self.assertEqual(config.model.base_url, "https://api.openai.com/v1")


class SecurityHelperTests(unittest.TestCase):
    def test_redact_password(self) -> None:
        out = redact_secrets({"username": "a", "password": "secret123"})
        self.assertEqual(out["password"], "***REDACTED***")
        self.assertEqual(out["username"], "a")

    def test_auth_optional(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            # Clear lru_cache on api_token
            from assistant import security

            security.api_token.cache_clear()
            self.assertTrue(check_request_auth({}))

    def test_auth_bearer(self) -> None:
        from assistant import security

        with mock.patch.dict(os.environ, {"THURSDAY_API_TOKEN": "tok123"}, clear=True):
            security.api_token.cache_clear()
            self.assertTrue(check_request_auth({"Authorization": "Bearer tok123"}))
            self.assertFalse(check_request_auth({"Authorization": "Bearer wrong"}))
            security.api_token.cache_clear()


if __name__ == "__main__":
    unittest.main()
