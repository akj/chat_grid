from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import cast

import pytest

from app.auth_service import AuthError, AuthService


def make_auth_service(tmp_path: Path) -> AuthService:
    return AuthService(
        db_path=tmp_path / "chatgrid.db",
        token_hash_secret="test-secret",
        password_min_length=8,
        password_max_length=32,
        username_min_length=2,
        username_max_length=32,
    )


def test_register_and_resume_session(tmp_path: Path) -> None:
    service = make_auth_service(tmp_path)
    try:
        session = service.register("User_One", "password99", email="a@example.com")
        assert session.user.username == "user_one"
        resumed = service.resume(session.token)
        assert resumed.user.id == session.user.id
        assert resumed.user.role == "user"
        assert "user.list" in resumed.user.permissions
    finally:
        service.close()


def test_last_position_persists_facing(tmp_path: Path) -> None:
    service = make_auth_service(tmp_path)
    try:
        session = service.register("facing_user", "password99")
        service.set_last_position(session.user.id, 3, 4, 0, 315)

        saved = service.get_user_by_id(session.user.id)
        assert saved is not None
        assert (saved.last_x, saved.last_y, saved.last_z) == (3, 4, 0)
        assert saved.last_facing_deg == 315
    finally:
        service.close()

    resumed_service = make_auth_service(tmp_path)
    try:
        resumed = resumed_service.login("facing_user", "password99")
        assert resumed.user.last_facing_deg == 315
    finally:
        resumed_service.close()


def test_existing_user_state_schema_migrates_facing_column(tmp_path: Path) -> None:
    db_path = tmp_path / "chatgrid.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE user_state (
                user_id INTEGER PRIMARY KEY,
                last_nickname TEXT,
                last_x INTEGER,
                last_y INTEGER,
                last_z INTEGER,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    service = make_auth_service(tmp_path)
    try:
        columns = {
            str(row[1])
            for row in service._conn.execute("PRAGMA table_info(user_state)")
        }
        assert "last_facing_deg" in columns
    finally:
        service.close()


def test_user_list_permission_defaults_to_user_and_editor(tmp_path: Path) -> None:
    service = make_auth_service(tmp_path)
    try:
        roles = {
            str(role["name"]): set(cast(list[str], role["permissions"]))
            for role in service.list_roles_with_counts()
        }
        assert "user.list" in roles["admin"]
        assert "user.list" in roles["editor"]
        assert "user.list" in roles["user"]
        assert "user.list" not in roles["guest"]
    finally:
        service.close()


def test_login_rejects_invalid_password(tmp_path: Path) -> None:
    service = make_auth_service(tmp_path)
    try:
        service.register("alpha", "password99")
        with pytest.raises(AuthError):
            service.login("alpha", "wrong-pass")
    finally:
        service.close()


def test_touch_last_seen_updates_admin_presence_timestamp(tmp_path: Path) -> None:
    service = make_auth_service(tmp_path)
    try:
        session = service.register("present_user", "password99")
        service.touch_last_seen(session.user.id, 1_800_000_000_000)

        user = next(
            entry
            for entry in service.list_users_for_admin()
            if entry["id"] == session.user.id
        )
        assert user["lastSeenAt"] == 1_800_000_000_000
    finally:
        service.close()


def test_bootstrap_admin_once(tmp_path: Path) -> None:
    service = make_auth_service(tmp_path)
    try:
        admin = service.bootstrap_admin("root-admin", "password99", email=None)
        assert admin.role == "admin"
        with pytest.raises(AuthError):
            service.bootstrap_admin("another-admin", "password99")
    finally:
        service.close()


def test_login_missing_user_runs_dummy_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = make_auth_service(tmp_path)
    try:
        calls: list[tuple[str, str]] = []

        def fake_verify(password: str, stored: str) -> bool:
            calls.append((password, stored))
            return False

        monkeypatch.setattr(service, "_verify_password", fake_verify)
        with pytest.raises(AuthError):
            service.login("missing_user", "password99")
        assert len(calls) == 1
        assert calls[0][0] == "password99"
    finally:
        service.close()


def test_delete_role_rejects_admin_and_user(tmp_path: Path) -> None:
    service = make_auth_service(tmp_path)
    try:
        with pytest.raises(AuthError):
            service.delete_role("admin", "editor")
        with pytest.raises(AuthError):
            service.delete_role("user", "editor")
    finally:
        service.close()


def test_update_role_permissions_rejects_admin(tmp_path: Path) -> None:
    service = make_auth_service(tmp_path)
    try:
        with pytest.raises(AuthError):
            service.update_role_permissions("admin", ["chat.send"])
    finally:
        service.close()


def test_delete_user_removes_account(tmp_path: Path) -> None:
    service = make_auth_service(tmp_path)
    try:
        service.register("alpha", "password99")
        deleted = service.delete_user("alpha")
        assert deleted == "alpha"
        with pytest.raises(AuthError):
            service.login("alpha", "password99")
    finally:
        service.close()
