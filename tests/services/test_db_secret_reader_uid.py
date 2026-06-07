"""komodo-mongo reads MONGO_INITDB_ROOT_PASSWORD_FILE as uid 999 (it gosu's to mongodb BEFORE
reading the _FILE), so a root:root 0600 secret is unreadable → EACCES crash-loop. The installer
must chown that secret to the reader uid. live-deploy 2026-06-07 komodo-mongo "Permission denied".
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.backend_any


def test_komodo_mongo_secret_reader_uid_is_999() -> None:
    from agmind.install.secret_keys import DB_SECRET_FILE_READER_UID

    assert DB_SECRET_FILE_READER_UID["komodo_mongo_password"] == 999


def test_postgres_mysql_are_root_read_no_chown() -> None:
    # postgres/mysql read their *_FILE while still root → must NOT be in the non-root chown map.
    from agmind.install.secret_keys import DB_SECRET_FILE_READER_UID

    assert "postgres_password" not in DB_SECRET_FILE_READER_UID
    assert "mysql_root_password" not in DB_SECRET_FILE_READER_UID


def test_write_secret_file_chowns_to_reader_uid(monkeypatch, tmp_path) -> None:
    from agmind.install import steps

    chowns: list[tuple[str, int, int]] = []
    monkeypatch.setattr(steps.os, "chown", lambda p, u, g: chowns.append((str(p), u, g)))
    target = tmp_path / "secrets" / "komodo_mongo_password"
    steps._write_secret_file(target, "s3cr3t", reader_uid=999)
    assert chowns == [(str(target), 999, 999)]
    assert target.read_text() == "s3cr3t"


def test_write_secret_file_root_read_does_not_chown(monkeypatch, tmp_path) -> None:
    from agmind.install import steps

    chowns: list[str] = []
    monkeypatch.setattr(steps.os, "chown", lambda p, u, g: chowns.append(str(p)))
    steps._write_secret_file(tmp_path / "secrets" / "postgres_password", "pw")  # reader_uid=None
    assert chowns == []
