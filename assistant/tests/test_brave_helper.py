from __future__ import annotations

import json

from assistant.integrations import brave_helper


def test_status_recognizes_matching_managed_policy(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    policy_path = tmp_path / "policy" / "thursday-helper.json"
    data_dir.mkdir()
    policy_path.parent.mkdir()
    (data_dir / "thursday-helper.crx").write_bytes(b"CRX")
    (data_dir / "installation.json").write_text(
        json.dumps({"extension_id": "a" * 32, "version": brave_helper.HELPER_VERSION})
    )
    policy_path.write_text(
        json.dumps(
            {
                "ExtensionInstallForcelist": [
                    f"{'a' * 32};{(data_dir / 'updates.xml').as_uri()}"
                ]
            }
        )
    )
    monkeypatch.setenv("THURSDAY_BRAVE_HELPER_HOME", str(data_dir))
    monkeypatch.setenv("THURSDAY_BRAVE_POLICY_PATH", str(policy_path))

    result = brave_helper.status()
    assert result["installed"]
    assert result["policy_active"]
    assert result["current"]


def test_status_does_not_accept_an_unrelated_policy(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    policy_path = tmp_path / "policy.json"
    data_dir.mkdir()
    (data_dir / "thursday-helper.crx").write_bytes(b"CRX")
    (data_dir / "installation.json").write_text(
        json.dumps({"extension_id": "a" * 32, "version": brave_helper.HELPER_VERSION})
    )
    policy_path.write_text(json.dumps({"ExtensionInstallForcelist": ["b" * 32]}))
    monkeypatch.setenv("THURSDAY_BRAVE_HELPER_HOME", str(data_dir))
    monkeypatch.setenv("THURSDAY_BRAVE_POLICY_PATH", str(policy_path))

    result = brave_helper.status()
    assert not result["policy_active"]
    assert not result["current"]
