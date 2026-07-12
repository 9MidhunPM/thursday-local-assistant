from __future__ import annotations

import os
import unittest
from unittest import mock

from assistant.llm.client import PROVIDER_PRESETS, resolve_provider_settings
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
