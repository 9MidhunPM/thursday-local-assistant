from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from assistant.voice import neural_setup as setup


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("THURSDAY_NEURAL_PYTHON", raising=False)
    monkeypatch.delenv("THURSDAY_NEURAL_MODELS", raising=False)
    contents = b"synthetic-model-data"
    asset = {
        "name": "model.onnx", "url": "https://example.invalid/model",
        "sha256": hashlib.sha256(contents).hexdigest(), "size": len(contents),
    }
    monkeypatch.setattr(setup, "ASSETS", (asset,))
    return tmp_path, contents, asset


def test_defaults_and_overrides_preserve_virtual_environment_symlink(isolated, monkeypatch):
    root, _, _ = isolated
    assert setup.python_path() == root / "data/thursday/neural-venv/bin/python"
    assert setup.models_dir() == root / "cache/thursday/voices/kokoro"
    interpreter = root / "other/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to("/usr/bin/python3")
    monkeypatch.setenv("THURSDAY_NEURAL_PYTHON", str(interpreter))
    monkeypatch.setenv("THURSDAY_NEURAL_MODELS", str(root / "models"))
    assert setup.python_path() == interpreter
    assert setup.models_dir() == root / "models"


def test_import_is_verified_private_idempotent_and_offline(isolated, monkeypatch):
    root, contents, asset = isolated
    source = root / "import"
    source.mkdir()
    (source / asset["name"]).write_bytes(contents)
    runtime = Mock()
    download = Mock(side_effect=AssertionError("network called"))
    monkeypatch.setattr(setup, "_install_runtime", runtime)
    monkeypatch.setattr(setup.urllib.request, "urlopen", download)
    assert setup.install(import_models=source) == {"installed": True, "model_verified": True}
    assert setup.install(import_models=source)["model_verified"]
    target = setup.models_dir()
    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / asset["name"]).stat().st_mode & 0o777 == 0o600
    assert (target / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((target / "manifest.json").read_text())["verified"] is True
    download.assert_not_called()


def test_bad_import_fails_before_runtime_mutation(isolated, monkeypatch):
    root, _, asset = isolated
    source = root / "import"
    source.mkdir()
    (source / asset["name"]).write_bytes(b"wrong")
    runtime = Mock()
    monkeypatch.setattr(setup, "_install_runtime", runtime)
    with pytest.raises(setup.SetupError, match="SHA256"):
        setup.install(import_models=source)
    runtime.assert_not_called()


def test_symlink_model_or_directory_is_refused(isolated):
    root, contents, asset = isolated
    original = root / "original"
    original.write_bytes(contents)
    source = root / "import"
    source.mkdir()
    (source / asset["name"]).symlink_to(original)
    assert not setup.verify_models(source)
    linked = root / "linked"
    linked.symlink_to(source, target_is_directory=True)
    with pytest.raises(setup.SetupError, match="symbolic"):
        setup._private_directory(linked / "nested")
    with pytest.raises(setup.SetupError, match="unsafe"):
        setup._install_asset(source, asset, None)


@pytest.mark.parametrize("payload", [b"bad", b"x" * 100])
def test_bad_download_never_becomes_usable(isolated, monkeypatch, payload):
    root, _, asset = isolated
    target = setup._private_directory(root / "download")
    monkeypatch.setattr(setup.urllib.request, "urlopen", Mock(return_value=io.BytesIO(payload)))
    with pytest.raises(setup.SetupError):
        setup._install_asset(target, asset, None)
    assert not (target / asset["name"]).exists()
    assert not list(target.iterdir())


def test_verified_download_is_published_only_after_hash_check(isolated, monkeypatch):
    root, contents, asset = isolated
    target = setup._private_directory(root / "download")
    fetch = Mock(return_value=io.BytesIO(contents))
    monkeypatch.setattr(setup.urllib.request, "urlopen", fetch)
    setup._install_asset(target, asset, None)
    assert setup.verify_models(target)
    fetch.assert_called_once_with(asset["url"], timeout=30)


def test_unmanaged_environment_is_not_modified(isolated, monkeypatch):
    root, _, _ = isolated
    directory = root / "data/thursday/neural-venv"
    directory.mkdir(parents=True)
    marker = directory / "user-file"
    marker.write_text("untouched")
    monkeypatch.setattr(setup.shutil, "which", Mock(return_value="/usr/bin/uv"))
    run = Mock()
    monkeypatch.setattr(setup, "_run", run)
    with pytest.raises(setup.SetupError, match="unmanaged"):
        setup._install_runtime("3.13")
    assert marker.read_text() == "untouched"
    run.assert_not_called()


@pytest.mark.usefixtures("isolated")
def test_runtime_install_is_hash_enforced_and_version_checked(monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", Mock(return_value="/usr/bin/uv"))
    identity = json.dumps({"version": [3, 13], "prefix": str(setup.python_path().parent.parent)})
    run = Mock(side_effect=["", identity, ""])
    monkeypatch.setattr(setup, "_run", run)
    setup._install_runtime("3.13")
    assert run.call_args_list[0].args[0][:4] == [
        "/usr/bin/uv", "venv", "--allow-existing", "--python",
    ]
    command = run.call_args_list[-1].args[0]
    assert "--require-hashes" in command
    assert "--only-binary" in command
    assert str(setup.python_path()) in command
    assert setup.python_path().parent.parent.stat().st_mode & 0o777 == 0o700


def test_wrong_python_or_missing_uv_is_actionable(isolated, monkeypatch):  # noqa: ARG001
    monkeypatch.setattr(setup.shutil, "which", Mock(return_value=None))
    with pytest.raises(setup.SetupError, match="Install uv"):
        setup._install_runtime("3.13")
    monkeypatch.setattr(setup.shutil, "which", Mock(return_value="/usr/bin/uv"))
    monkeypatch.setattr(setup, "_run", Mock(side_effect=["", '{"version":[3,14]}']))
    with pytest.raises(setup.SetupError, match=r"Python 3\.13"):
        setup._install_runtime("3.14")


@pytest.mark.usefixtures("isolated")
def test_runtime_cannot_install_into_a_global_interpreter(monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", Mock(return_value="/usr/bin/uv"))
    run = Mock(side_effect=["", '{"version":[3,13],"prefix":"/usr"}'])
    monkeypatch.setattr(setup, "_run", run)
    with pytest.raises(setup.SetupError, match="outside"):
        setup._install_runtime("3.13")
    assert run.call_count == 2


def test_setup_cli_sanitizes_failures_and_restores_umask(monkeypatch, capsys):
    monkeypatch.setattr(setup, "install", Mock(side_effect=OSError("private-secret")))
    previous = os.umask(0o022)
    try:
        assert setup.main([]) == 1
        output = capsys.readouterr().out
        assert "private-secret" not in output
        assert "Python 3.13" in output
        assert os.umask(0o022) == 0o022
    finally:
        os.umask(previous)


def test_lock_contains_only_exact_hash_pins():
    path = Path(setup.__file__).with_name("neural-requirements.txt")
    entries = path.read_text().replace("\\\n", " ").splitlines()
    packages = []
    for entry in entries:
        if not entry.strip() or entry.startswith("#"):
            continue
        assert "==" in entry
        assert "--hash=sha256:" in entry
        assert "http" not in entry
        packages.append(entry.split("==")[0])
    assert len(packages) == 13
    assert {"kokoro-onnx", "onnxruntime", "espeakng-loader"}.issubset(packages)
