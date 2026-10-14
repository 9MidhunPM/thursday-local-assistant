from __future__ import annotations

import fcntl

import pytest

from assistant import main


def test_duplicate_launcher_does_not_construct_or_interrupt_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "load_dotenv", lambda *_args: False)
    monkeypatch.setattr("sys.argv", ["thursday", "--web", "--no-browser"])

    def no_runtime(*_args):
        pytest.fail("A duplicate launcher must not open or mutate task storage")

    def locked(*_args):
        raise BlockingIOError

    monkeypatch.setattr(main, "build_runtime", no_runtime)
    monkeypatch.setattr(fcntl, "flock", locked)
    with pytest.raises(SystemExit) as stopped:
        main.main()
    assert stopped.value.code == 0
    assert (tmp_path / "server.lock").stat().st_mode & 0o777 == 0o600
