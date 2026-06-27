"""Tests for state.update_env_file hardening (validate / atomic / backup)."""

import os

os.environ.setdefault("APPRISE_URL", "json://localhost/")

import state  # noqa: E402


def test_update_preserves_comments_order_and_backs_up(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text(
        "# header comment\n"
        "APPRISE_URL=discord://a/b,\n"
        "ID_LIST=abc123,\n"
        "\n"
        "# trailing comment\n"
        "HEARTBEAT_INTERVAL=6\n"
    )

    assert state.update_env_file("ID_LIST", ["abc123", "def456"]) is True

    content = env.read_text()
    # Comments preserved.
    assert "# header comment" in content
    assert "# trailing comment" in content
    # Ordering of the surrounding keys preserved.
    assert (
        content.index("APPRISE_URL=")
        < content.index("ID_LIST=")
        < content.index("HEARTBEAT_INTERVAL=")
    )
    # New value written.
    assert "def456" in content
    # Backup holds the previous version (atomic replace left a .env.bak).
    backup = tmp_path / ".env.bak"
    assert backup.exists()
    assert "def456" not in backup.read_text()
    # No leftover temp files.
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_update_aborts_on_invalid_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    original = "ID_LIST=abc,\n"
    env.write_text(original)

    assert state.update_env_file("bad key!", ["x"]) is False
    # File untouched and no backup written.
    assert env.read_text() == original
    assert not (tmp_path / ".env.bak").exists()


def test_update_aborts_on_newline_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    original = "ID_LIST=abc,\n"
    env.write_text(original)

    assert state.update_env_file("ID_LIST", ["x\ny"]) is False
    assert env.read_text() == original
