"""Account, role, permission, and session persistence service for websocket authentication."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import logging
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


SESSION_TTL_MS = 14 * 24 * 60 * 60 * 1000
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536
ARGON2_PARALLELISM = 1
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16
USERNAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")
ROLE_NAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")
LOGGER = logging.getLogger("chgrid.server.auth")

PERMISSIONS: tuple[str, ...] = (
    "item.create",
    "item.edit.own",
    "item.edit.any",
    "item.delete.own",
    "item.delete.any",
    "item.use",
    "item.pickup_drop.own",
    "item.pickup_drop.any",
    "item.transfer.own",
    "item.transfer.any",
    "chat.send",
    "voice.send",
    "profile.update_nickname",
    "account.delete.any",
    "user.list",
    "user.ban_unban",
    "user.change_role",
    "role.manage",
    "server.manage_settings",
    "server.allow_reboot",
    "world.structure.edit",
)

PERMISSION_DESCRIPTIONS: dict[str, str] = {
    "item.create": "Allow creating new items.",
    "item.edit.own": "Allow editing items created by this user.",
    "item.edit.any": "Allow editing any item.",
    "item.delete.own": "Allow deleting items created by this user.",
    "item.delete.any": "Allow deleting any item.",
    "item.use": "Allow using item primary and secondary actions.",
    "item.pickup_drop.own": "Allow picking up and dropping items created by this user.",
    "item.pickup_drop.any": "Allow picking up and dropping any item.",
    "item.transfer.own": "Allow transferring items created by this user to another user.",
    "item.transfer.any": "Allow transferring any item to another user.",
    "chat.send": "Allow sending chat messages.",
    "voice.send": "Allow transmitting microphone audio.",
    "profile.update_nickname": "Allow changing nickname.",
    "account.delete.any": "Allow deleting other user accounts.",
    "user.list": "Allow listing registered users and their presence.",
    "user.ban_unban": "Allow banning and unbanning users.",
    "user.change_role": "Allow assigning user roles.",
    "role.manage": "Allow creating, editing, and deleting roles.",
    "server.manage_settings": "Allow changing server settings.",
    "server.allow_reboot": "Allow scheduling a server reboot from chat command.",
    "world.structure.edit": "Allow live editing of World Builder structures.",
}

DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": set(PERMISSIONS),
    "editor": {
        "item.create",
        "item.edit.own",
        "item.edit.any",
        "item.delete.own",
        "item.delete.any",
        "item.use",
        "item.pickup_drop.own",
        "item.pickup_drop.any",
        "item.transfer.own",
        "item.transfer.any",
        "chat.send",
        "voice.send",
        "profile.update_nickname",
        "user.list",
        "world.structure.edit",
    },
    "user": {
        "item.create",
        "item.edit.own",
        "item.delete.own",
        "item.use",
        "item.pickup_drop.own",
        "item.transfer.own",
        "chat.send",
        "voice.send",
        "profile.update_nickname",
        "user.list",
    },
    "guest": {
        "item.use",
        "chat.send",
        "voice.send",
        "profile.update_nickname",
    },
}
DEFAULT_ROLE_ORDER: tuple[str, ...] = ("admin", "editor", "user", "guest")


def _build_dummy_password_hash(password_hasher: PasswordHasher) -> str:
    """Build one deterministic Argon2id hash used to equalize login miss timing."""

    return password_hasher.hash("chgrid_dummy_password")


@dataclass(frozen=True)
class AuthUser:
    """Authenticated account identity details."""

    id: str
    username: str
    role: str
    permissions: tuple[str, ...]
    status: str
    email: str | None
    last_nickname: str | None
    last_x: int | None
    last_y: int | None
    last_z: int | None
    last_facing_deg: int | None


@dataclass(frozen=True)
class AuthSession:
    """Session validation result with user identity."""

    session_id: str
    token: str
    user: AuthUser


class AuthError(ValueError):
    """Raised when authentication input or policy checks fail."""


class AuthService:
    """Manages account registration, roles/permissions, and rolling session validation."""

    def __init__(
        self,
        db_path: Path,
        token_hash_secret: str,
        password_min_length: int,
        password_max_length: int,
        username_min_length: int,
        username_max_length: int,
    ):
        """Initialize auth database connection and schema."""

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.password_min_length = max(1, int(password_min_length))
        self.password_max_length = max(
            self.password_min_length, int(password_max_length)
        )
        self.username_min_length = max(1, int(username_min_length))
        self.username_max_length = max(
            self.username_min_length, int(username_max_length)
        )
        secret = token_hash_secret.strip()
        if not secret:
            raise AuthError("CHGRID_AUTH_SECRET is required when auth is enabled.")
        self._token_secret = secret.encode("utf-8")
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn_lock = threading.RLock()
        self._password_hasher = PasswordHasher(
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_COST,
            parallelism=ARGON2_PARALLELISM,
            hash_len=ARGON2_HASH_LEN,
            salt_len=ARGON2_SALT_LEN,
        )
        self._dummy_password_hash = _build_dummy_password_hash(self._password_hasher)
        self._ensure_schema()

    def close(self) -> None:
        """Close the underlying SQLite connection."""

        with self._conn_lock:
            self._conn.close()

    def bootstrap_admin(
        self, username: str, password: str, email: str | None = None
    ) -> AuthUser:
        """Create the first admin account, or fail if one already exists."""

        if self.has_admin():
            raise AuthError("An admin account already exists.")
        created = self.register(username, password, email=email, role="admin")
        return created.user

    def has_admin(self) -> bool:
        """Return True when at least one active admin account exists."""

        existing = self._db_fetchone(
            """
            SELECT 1
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE r.name = 'admin' AND u.status = 'active'
            LIMIT 1
            """
        )
        return existing is not None

    def list_all_permissions(self) -> list[str]:
        """Return canonical sorted permission key list."""

        return list(PERMISSIONS)

    def list_all_permission_descriptions(self) -> dict[str, str]:
        """Return canonical permission tooltip text keyed by permission id."""

        return {key: PERMISSION_DESCRIPTIONS.get(key, key) for key in PERMISSIONS}

    def get_user_permissions(self, user_id: str) -> set[str]:
        """Return current permission set for one user id."""

        try:
            user_id_value = int(user_id)
        except (TypeError, ValueError):
            return set()
        rows = self._db_fetchall(
            """
            SELECT rp.permission_key
            FROM users u
            JOIN role_permissions rp ON rp.role_id = u.role_id
            WHERE u.id = ?
            """,
            (user_id_value,),
        )
        return {str(row["permission_key"]) for row in rows}

    def has_permission(self, user_id: str, permission_key: str) -> bool:
        """Return whether one user currently has a specific permission key."""

        return permission_key in self.get_user_permissions(user_id)

    def get_username_by_id(self, user_id: str) -> str | None:
        """Return username for one numeric user id, or None when not found."""

        try:
            user_id_value = int(user_id)
        except (TypeError, ValueError):
            return None
        row = self._db_fetchone(
            "SELECT username FROM users WHERE id = ?", (user_id_value,)
        )
        if row is None:
            return None
        return str(row["username"])

    def list_roles_with_counts(self) -> list[dict[str, object]]:
        """Return all roles with permission sets and assigned-user counts."""

        rows = self._db_fetchall(
            """
            SELECT
                r.id,
                r.name,
                r.is_system,
                COUNT(u.id) AS user_count
            FROM roles r
            LEFT JOIN users u ON u.role_id = r.id
            GROUP BY r.id
            ORDER BY r.name ASC
            """
        )
        permissions_by_role = self._permissions_by_role_id()
        return [
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "isSystem": bool(int(row["is_system"])),
                "userCount": int(row["user_count"]),
                "permissions": sorted(
                    list(permissions_by_role.get(int(row["id"]), set()))
                ),
            }
            for row in rows
        ]

    def create_role(self, name: str) -> dict[str, object]:
        """Create one custom role with no permissions."""

        normalized = self._normalize_role_name(name)
        self._validate_role_name(normalized)
        now_ms = self.now_ms()
        try:
            self._db_execute(
                "INSERT INTO roles (name, is_system, created_at_ms, updated_at_ms) VALUES (?, 0, ?, ?)",
                (normalized, now_ms, now_ms),
            )
            self._db_commit()
        except sqlite3.IntegrityError as exc:
            raise AuthError("Role already exists.") from exc
        role = self.get_role_by_name(normalized)
        if role is None:
            raise AuthError("Failed to create role.")
        return role

    def get_role_by_name(self, role_name: str) -> dict[str, object] | None:
        """Return one role metadata row by normalized role name."""

        normalized = self._normalize_role_name(role_name)
        row = self._db_fetchone(
            "SELECT id, name, is_system FROM roles WHERE name = ?", (normalized,)
        )
        if row is None:
            return None
        permissions = self._permissions_by_role_id().get(int(row["id"]), set())
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "isSystem": bool(int(row["is_system"])),
            "permissions": sorted(list(permissions)),
        }

    def update_role_permissions(
        self, role_name: str, permission_keys: list[str]
    ) -> set[str]:
        """Replace one role's permission assignment with validated keys."""

        normalized_role = self._normalize_role_name(role_name)
        if normalized_role == "admin":
            raise AuthError("Admin role permissions are locked on.")
        role_row = self._db_fetchone(
            "SELECT id, name FROM roles WHERE name = ?", (normalized_role,)
        )
        if role_row is None:
            raise AuthError("Role not found.")

        validated = self._validate_permission_keys(permission_keys)
        role_id = int(role_row["id"])
        now_ms = self.now_ms()

        self._db_execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
        for key in sorted(validated):
            self._db_execute(
                "INSERT INTO role_permissions (role_id, permission_key) VALUES (?, ?)",
                (role_id, key),
            )
        self._db_execute(
            "UPDATE roles SET updated_at_ms = ? WHERE id = ?", (now_ms, role_id)
        )
        self._db_commit()
        return validated

    def delete_role(
        self, role_name: str, replacement_role_name: str
    ) -> tuple[list[str], str]:
        """Delete one role, reassigning users to a replacement role."""

        normalized_role = self._normalize_role_name(role_name)
        normalized_replacement = self._normalize_role_name(replacement_role_name)
        if normalized_role in {"admin", "user"}:
            raise AuthError("Admin and user roles cannot be deleted.")
        if normalized_role == normalized_replacement:
            raise AuthError("Replacement role must differ from deleted role.")

        role_row = self._db_fetchone(
            "SELECT id FROM roles WHERE name = ?", (normalized_role,)
        )
        replacement_row = self._db_fetchone(
            "SELECT id FROM roles WHERE name = ?", (normalized_replacement,)
        )
        if role_row is None:
            raise AuthError("Role not found.")
        if replacement_row is None:
            raise AuthError("Replacement role not found.")

        role_id = int(role_row["id"])
        replacement_id = int(replacement_row["id"])
        affected_rows = self._db_fetchall(
            "SELECT username FROM users WHERE role_id = ?", (role_id,)
        )
        affected_usernames = [str(row["username"]) for row in affected_rows]

        self._db_execute(
            "UPDATE users SET role_id = ?, updated_at_ms = ? WHERE role_id = ?",
            (replacement_id, self.now_ms(), role_id),
        )
        self._db_execute("DELETE FROM roles WHERE id = ?", (role_id,))
        self._db_commit()
        return affected_usernames, normalized_replacement

    def list_users_for_admin(self) -> list[dict[str, str | int]]:
        """Return users ordered alphabetically with role, status, and presence."""

        rows = self._db_fetchall(
            """
            SELECT u.id, u.username, r.name AS role_name, u.status,
                   u.last_seen_at_ms
            FROM users u
            JOIN roles r ON r.id = u.role_id
            ORDER BY u.username COLLATE NOCASE ASC
            """
        )
        return [
            {
                "id": str(row["id"]),
                "username": str(row["username"]),
                "role": str(row["role_name"]),
                "status": str(row["status"]),
                "lastSeenAt": int(row["last_seen_at_ms"]),
            }
            for row in rows
        ]

    def set_user_role(
        self, target_username: str, role_name: str, *, actor_user_id: str | None = None
    ) -> str:
        """Assign one user's role by normalized role name."""

        normalized_username = self._normalize_username(target_username)
        normalized_role = self._normalize_role_name(role_name)
        role_row = self._db_fetchone(
            "SELECT id FROM roles WHERE name = ?", (normalized_role,)
        )
        if role_row is None:
            raise AuthError("Role not found.")
        user_row = self._db_fetchone(
            """
            SELECT u.id, u.status, r.name AS role_name
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE u.username = ?
            """,
            (normalized_username,),
        )
        if user_row is None:
            raise AuthError("User not found.")

        current_role = str(user_row["role_name"])
        if (
            current_role == "admin"
            and normalized_role != "admin"
            and self._active_admin_count() <= 1
        ):
            raise AuthError("Cannot change role for the last active admin.")
        if actor_user_id is not None and str(user_row["id"]) == str(actor_user_id):
            if (
                current_role == "admin"
                and normalized_role != "admin"
                and self._active_admin_count() <= 1
            ):
                raise AuthError("Cannot self-demote the last active admin.")

        self._db_execute(
            "UPDATE users SET role_id = ?, updated_at_ms = ? WHERE id = ?",
            (int(role_row["id"]), self.now_ms(), int(user_row["id"])),
        )
        self._db_commit()
        return normalized_username

    def set_user_status(self, target_username: str, status: str) -> str:
        """Set one account status to active/disabled."""

        normalized_username = self._normalize_username(target_username)
        normalized_status = status.strip().lower()
        if normalized_status not in {"active", "disabled"}:
            raise AuthError("Invalid status.")
        user_row = self._db_fetchone(
            """
            SELECT u.id, u.status, r.name AS role_name
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE u.username = ?
            """,
            (normalized_username,),
        )
        if user_row is None:
            raise AuthError("User not found.")
        current_status = str(user_row["status"])
        current_role = str(user_row["role_name"])
        if (
            current_role == "admin"
            and current_status == "active"
            and normalized_status != "active"
            and self._active_admin_count() <= 1
        ):
            raise AuthError("Cannot disable the last active admin.")
        self._db_execute(
            "UPDATE users SET status = ?, updated_at_ms = ? WHERE id = ?",
            (normalized_status, self.now_ms(), int(user_row["id"])),
        )
        self._db_commit()
        return normalized_username

    def delete_user(
        self, target_username: str, *, actor_user_id: str | None = None
    ) -> str:
        """Delete one account and related session/state rows."""

        normalized_username = self._normalize_username(target_username)
        user_row = self._db_fetchone(
            """
            SELECT u.id, u.status, r.name AS role_name
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE u.username = ?
            """,
            (normalized_username,),
        )
        if user_row is None:
            raise AuthError("User not found.")
        user_id = int(user_row["id"])
        current_status = str(user_row["status"])
        current_role = str(user_row["role_name"])
        if (
            current_role == "admin"
            and current_status == "active"
            and self._active_admin_count() <= 1
        ):
            raise AuthError("Cannot delete the last active admin.")
        if actor_user_id is not None and str(user_id) == str(actor_user_id):
            if (
                current_role == "admin"
                and current_status == "active"
                and self._active_admin_count() <= 1
            ):
                raise AuthError(
                    "Cannot delete your own account while you are the last active admin."
                )
        self._db_execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self._db_execute("DELETE FROM user_state WHERE user_id = ?", (user_id,))
        self._db_execute("DELETE FROM users WHERE id = ?", (user_id,))
        self._db_commit()
        return normalized_username

    def get_user_by_id(self, user_id: str) -> AuthUser | None:
        """Return one user by id with current role and permissions."""

        try:
            user_id_value = int(user_id)
        except (TypeError, ValueError):
            return None
        row = self._db_fetchone(
            """
            SELECT
                u.id,
                u.username,
                r.name AS role_name,
                u.status,
                u.email,
                us.last_nickname,
                us.last_x,
                us.last_y,
                us.last_z,
                us.last_facing_deg
            FROM users u
            JOIN roles r ON r.id = u.role_id
            LEFT JOIN user_state us ON us.user_id = u.id
            WHERE u.id = ?
            """,
            (user_id_value,),
        )
        if row is None:
            return None
        return self._row_to_user(row)

    def list_connected_user_ids_for_role(self, role_name: str) -> list[str]:
        """Return user id strings currently assigned to one role name."""

        normalized = self._normalize_role_name(role_name)
        rows = self._db_fetchall(
            """
            SELECT u.id
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE r.name = ?
            """,
            (normalized,),
        )
        return [str(row["id"]) for row in rows]

    def get_user_id_by_username(self, username: str) -> str | None:
        """Return user id for one username, or None when missing."""

        normalized = self._normalize_username(username)
        row = self._db_fetchone(
            "SELECT id FROM users WHERE username = ?", (normalized,)
        )
        if row is None:
            return None
        return str(row["id"])

    def register(
        self,
        username: str,
        password: str,
        *,
        email: str | None = None,
        role: str = "user",
    ) -> AuthSession:
        """Register an account and issue a session token."""

        normalized_username = self._normalize_username(username)
        normalized_role = self._normalize_role_name(role)
        try:
            self._validate_username(normalized_username)
            self._validate_password(password)
            self._validate_role_name(normalized_role)
            normalized_email = self._normalize_email(email)
            role_row = self._db_fetchone(
                "SELECT id FROM roles WHERE name = ?", (normalized_role,)
            )
            if role_row is None:
                raise AuthError("Role not found.")
            now_ms = self.now_ms()
            password_hash = self._hash_password(password)
            self._db_execute(
                """
                INSERT INTO users (
                    username, password_hash, email, role_id, status, created_at_ms, updated_at_ms, last_login_at_ms, last_seen_at_ms
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    normalized_username,
                    password_hash,
                    normalized_email,
                    int(role_row["id"]),
                    now_ms,
                    now_ms,
                    now_ms,
                    now_ms,
                ),
            )
            self._db_commit()
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            if "users.username" in message:
                LOGGER.warning(
                    "register rejected username_taken username=%s", normalized_username
                )
                raise AuthError("Username is already taken.") from exc
            if "users.email" in message:
                LOGGER.warning(
                    "register rejected email_taken username=%s", normalized_username
                )
                raise AuthError("Email is already in use.") from exc
            LOGGER.exception(
                "register sqlite integrity failure username=%s", normalized_username
            )
            raise AuthError(
                "Registration failed due to a database constraint."
            ) from exc
        except AuthError as exc:
            LOGGER.warning(
                "register rejected username=%s reason=%s", normalized_username, str(exc)
            )
            raise
        except Exception as exc:
            LOGGER.exception(
                "register unexpected failure username=%s", normalized_username
            )
            raise AuthError("Registration failed due to a server error.") from exc

        user = self._get_user_by_username(normalized_username)
        if user is None:
            LOGGER.error(
                "register created user missing username=%s", normalized_username
            )
            raise AuthError("Failed to load newly created user.")
        self._db_execute(
            """
            INSERT OR IGNORE INTO user_state (user_id, last_nickname, last_x, last_y, last_z, last_facing_deg, updated_at_ms)
            VALUES (?, ?, NULL, NULL, NULL, NULL, ?)
            """,
            (int(user.id), user.username, self.now_ms()),
        )
        self._db_commit()
        user = AuthUser(
            id=user.id,
            username=user.username,
            role=user.role,
            permissions=user.permissions,
            status=user.status,
            email=user.email,
            last_nickname=user.username,
            last_x=user.last_x,
            last_y=user.last_y,
            last_z=user.last_z,
            last_facing_deg=user.last_facing_deg,
        )
        return self._create_session(user)

    def login(self, username: str, password: str) -> AuthSession:
        """Authenticate credentials and issue a fresh session."""

        normalized_username = self._normalize_username(username)
        user_row = self._db_fetchone(
            """
            SELECT
                u.id,
                u.username,
                u.password_hash,
                u.email,
                r.name AS role_name,
                u.status,
                us.last_nickname,
                us.last_x,
                us.last_y,
                us.last_z,
                us.last_facing_deg
            FROM users u
            JOIN roles r ON r.id = u.role_id
            LEFT JOIN user_state us ON us.user_id = u.id
            WHERE u.username = ?
            """,
            (normalized_username,),
        )
        if user_row is None:
            self._verify_password(password, self._dummy_password_hash)
            raise AuthError("Invalid username or password.")
        if user_row["status"] != "active":
            raise AuthError("Account is disabled.")
        if not self._verify_password(password, user_row["password_hash"]):
            raise AuthError("Invalid username or password.")
        if self._password_hasher.check_needs_rehash(user_row["password_hash"]):
            self._db_execute(
                "UPDATE users SET password_hash = ?, updated_at_ms = ? WHERE id = ?",
                (self._hash_password(password), self.now_ms(), user_row["id"]),
            )
        user = self._row_to_user(user_row)
        if not user.last_nickname:
            self.set_last_nickname(user.id, user.username)
            user = AuthUser(
                id=user.id,
                username=user.username,
                role=user.role,
                permissions=user.permissions,
                status=user.status,
                email=user.email,
                last_nickname=user.username,
                last_x=user.last_x,
                last_y=user.last_y,
                last_z=user.last_z,
                last_facing_deg=user.last_facing_deg,
            )
        now_ms = self.now_ms()
        self._db_execute(
            "UPDATE users SET last_login_at_ms = ?, updated_at_ms = ? WHERE id = ?",
            (now_ms, now_ms, user.id),
        )
        self._db_commit()
        return self._create_session(user)

    def resume(self, token: str) -> AuthSession:
        """Validate a session token and apply rolling expiry."""

        cleaned = token.strip()
        if not cleaned:
            raise AuthError("Missing session token.")
        token_hash = self._hash_token(cleaned)
        row = self._db_fetchone(
            """
            SELECT s.id AS session_id, s.user_id, s.expires_at_ms, s.revoked_at_ms,
                   u.username, r.name AS role_name, u.status, u.email, us.last_nickname, us.last_x, us.last_y, us.last_z, us.last_facing_deg
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            JOIN roles r ON r.id = u.role_id
            LEFT JOIN user_state us ON us.user_id = u.id
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        )
        if row is None:
            raise AuthError("Invalid session.")
        if row["revoked_at_ms"] is not None:
            raise AuthError("Session has been revoked.")
        now_ms = self.now_ms()
        if int(row["expires_at_ms"]) <= now_ms:
            self._db_execute(
                "UPDATE sessions SET revoked_at_ms = ? WHERE id = ?",
                (now_ms, row["session_id"]),
            )
            self._db_commit()
            raise AuthError("Session has expired.")
        if row["status"] != "active":
            raise AuthError("Account is disabled.")
        new_expiry = now_ms + SESSION_TTL_MS
        self._db_execute(
            "UPDATE sessions SET last_seen_at_ms = ?, expires_at_ms = ? WHERE id = ?",
            (now_ms, new_expiry, row["session_id"]),
        )
        self._db_commit()
        user = AuthUser(
            id=str(row["user_id"]),
            username=row["username"],
            role=row["role_name"],
            permissions=tuple(sorted(self.get_user_permissions(str(row["user_id"])))),
            status=row["status"],
            email=row["email"],
            last_nickname=row["last_nickname"],
            last_x=row["last_x"] if "last_x" in row.keys() else None,
            last_y=row["last_y"] if "last_y" in row.keys() else None,
            last_z=row["last_z"] if "last_z" in row.keys() else None,
            last_facing_deg=row["last_facing_deg"]
            if "last_facing_deg" in row.keys()
            else None,
        )
        if not user.last_nickname:
            self.set_last_nickname(user.id, user.username)
            user = AuthUser(
                id=user.id,
                username=user.username,
                role=user.role,
                permissions=user.permissions,
                status=user.status,
                email=user.email,
                last_nickname=user.username,
                last_x=user.last_x,
                last_y=user.last_y,
                last_z=user.last_z,
                last_facing_deg=user.last_facing_deg,
            )
        return AuthSession(session_id=str(row["session_id"]), token=cleaned, user=user)

    def revoke(self, token: str) -> None:
        """Revoke a session token if it exists."""

        cleaned = token.strip()
        if not cleaned:
            return
        token_hash = self._hash_token(cleaned)
        self._db_execute(
            "UPDATE sessions SET revoked_at_ms = ? WHERE token_hash = ? AND revoked_at_ms IS NULL",
            (self.now_ms(), token_hash),
        )
        self._db_commit()

    def set_last_nickname(self, user_id: str, nickname: str) -> None:
        """Persist the most recent nickname for one user."""

        cleaned = nickname.strip()
        if not cleaned:
            return
        try:
            user_id_value = int(user_id)
        except (TypeError, ValueError):
            return
        try:
            self._db_execute(
                """
                INSERT INTO user_state (user_id, last_nickname, last_x, last_y, last_z, last_facing_deg, updated_at_ms)
                VALUES (?, ?, NULL, NULL, NULL, NULL, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_nickname = excluded.last_nickname,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (user_id_value, cleaned, self.now_ms()),
            )
            self._db_commit()
        except sqlite3.IntegrityError:
            self._db_rollback()

    def set_last_position(
        self, user_id: str, x: int, y: int, z: int, facing_deg: int = 0
    ) -> None:
        """Persist last known world position and facing for one user."""

        try:
            user_id_value = int(user_id)
        except (TypeError, ValueError):
            return
        try:
            self._db_execute(
                """
                INSERT INTO user_state (user_id, last_nickname, last_x, last_y, last_z, last_facing_deg, updated_at_ms)
                VALUES (?, NULL, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_x = excluded.last_x,
                    last_y = excluded.last_y,
                    last_z = excluded.last_z,
                    last_facing_deg = excluded.last_facing_deg,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    user_id_value,
                    int(x),
                    int(y),
                    int(z),
                    int(facing_deg),
                    self.now_ms(),
                ),
            )
            self._db_commit()
        except sqlite3.IntegrityError:
            self._db_rollback()

    def touch_last_seen(self, user_id: str, seen_at_ms: int | None = None) -> int:
        """Record when an authenticated user was most recently present on the grid."""

        try:
            user_id_value = int(user_id)
        except (TypeError, ValueError) as exc:
            raise AuthError("Invalid user id.") from exc
        timestamp_ms = self.now_ms() if seen_at_ms is None else int(seen_at_ms)
        self._db_execute(
            "UPDATE users SET last_seen_at_ms = ? WHERE id = ?",
            (timestamp_ms, user_id_value),
        )
        self._db_commit()
        return timestamp_ms

    @staticmethod
    def now_ms() -> int:
        """Return unix epoch timestamp in milliseconds."""

        return int(time.time() * 1000)

    def _ensure_schema(self) -> None:
        """Create required auth tables and indexes when missing."""

        self._db_execute("PRAGMA foreign_keys = ON")

        self._db_execute(
            """
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                is_system INTEGER NOT NULL DEFAULT 0,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        self._db_execute(
            """
            CREATE TABLE IF NOT EXISTS permissions (
                key TEXT PRIMARY KEY,
                description TEXT NOT NULL
            )
            """
        )
        self._db_execute(
            """
            CREATE TABLE IF NOT EXISTS role_permissions (
                role_id INTEGER NOT NULL,
                permission_key TEXT NOT NULL,
                PRIMARY KEY(role_id, permission_key),
                FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE,
                FOREIGN KEY(permission_key) REFERENCES permissions(key) ON DELETE CASCADE
            )
            """
        )

        self._db_execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email TEXT UNIQUE,
                role_id INTEGER,
                status TEXT NOT NULL CHECK(status IN ('active', 'disabled')) DEFAULT 'active',
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                last_login_at_ms INTEGER,
                last_seen_at_ms INTEGER NOT NULL,
                FOREIGN KEY(role_id) REFERENCES roles(id)
            )
            """
        )

        user_cols = {
            str(row["name"]) for row in self._db_fetchall("PRAGMA table_info(users)")
        }
        if "role_id" not in user_cols:
            self._db_execute("ALTER TABLE users ADD COLUMN role_id INTEGER")
            user_cols.add("role_id")
        if "status" not in user_cols:
            self._db_execute(
                "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )
        if "created_at_ms" not in user_cols:
            self._db_execute(
                "ALTER TABLE users ADD COLUMN created_at_ms INTEGER NOT NULL DEFAULT 0"
            )
        if "updated_at_ms" not in user_cols:
            self._db_execute(
                "ALTER TABLE users ADD COLUMN updated_at_ms INTEGER NOT NULL DEFAULT 0"
            )
        if "last_login_at_ms" not in user_cols:
            self._db_execute("ALTER TABLE users ADD COLUMN last_login_at_ms INTEGER")
        if "last_seen_at_ms" not in user_cols:
            self._db_execute(
                "ALTER TABLE users ADD COLUMN last_seen_at_ms INTEGER NOT NULL DEFAULT 0"
            )
            self._db_execute(
                "UPDATE users SET last_seen_at_ms = COALESCE(last_login_at_ms, created_at_ms)"
            )
        if "email" not in user_cols:
            self._db_execute("ALTER TABLE users ADD COLUMN email TEXT")

        self._db_execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at_ms INTEGER NOT NULL,
                last_seen_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                revoked_at_ms INTEGER,
                ip TEXT,
                user_agent TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        self._db_execute(
            """
            CREATE TABLE IF NOT EXISTS user_state (
                user_id INTEGER PRIMARY KEY,
                last_nickname TEXT,
                last_x INTEGER,
                last_y INTEGER,
                last_z INTEGER,
                last_facing_deg INTEGER,
                updated_at_ms INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        user_state_cols = {
            str(row["name"])
            for row in self._db_fetchall("PRAGMA table_info(user_state)")
        }
        if "last_z" not in user_state_cols:
            self._db_execute("ALTER TABLE user_state ADD COLUMN last_z INTEGER")
        if "last_facing_deg" not in user_state_cols:
            self._db_execute(
                "ALTER TABLE user_state ADD COLUMN last_facing_deg INTEGER"
            )

        self._seed_permissions_and_roles()
        self._backfill_user_roles()

        self._db_execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)"
        )
        self._db_execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL"
        )
        self._db_execute(
            "CREATE INDEX IF NOT EXISTS idx_users_role_id ON users(role_id)"
        )
        self._db_execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)"
        )
        self._db_execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at_ms)"
        )
        self._db_execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash)"
        )
        self._db_execute(
            "CREATE INDEX IF NOT EXISTS idx_user_state_updated ON user_state(updated_at_ms)"
        )
        self._db_commit()

    def _seed_permissions_and_roles(self) -> None:
        """Insert canonical permissions and default roles when missing."""

        now_ms = self.now_ms()
        existing_permission_keys = {
            str(row["key"]) for row in self._db_fetchall("SELECT key FROM permissions")
        }
        for key in PERMISSIONS:
            description = PERMISSION_DESCRIPTIONS.get(key, key)
            self._db_execute(
                "INSERT OR IGNORE INTO permissions (key, description) VALUES (?, ?)",
                (key, description),
            )

        for role_name in DEFAULT_ROLE_ORDER:
            self._db_execute(
                """
                INSERT OR IGNORE INTO roles (name, is_system, created_at_ms, updated_at_ms)
                VALUES (?, 1, ?, ?)
                """,
                (role_name, now_ms, now_ms),
            )

        role_id_by_name = self._role_id_by_name()
        for role_name in DEFAULT_ROLE_ORDER:
            role_id = role_id_by_name.get(role_name)
            if role_id is None:
                continue
            if role_name == "admin":
                # Keep admin as superuser role: always full permission set.
                self._db_execute(
                    "DELETE FROM role_permissions WHERE role_id = ?", (role_id,)
                )
                allowed = set(PERMISSIONS)
            else:
                existing = self._db_fetchall(
                    "SELECT permission_key FROM role_permissions WHERE role_id = ?",
                    (role_id,),
                )
                if existing:
                    # Preserve existing customizations for non-admin defaults.
                    continue
                allowed = DEFAULT_ROLE_PERMISSIONS.get(role_name, set())
            for key in sorted(allowed):
                self._db_execute(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_key) VALUES (?, ?)",
                    (role_id, key),
                )

        # Grant newly introduced default permissions once to existing built-in
        # roles without overwriting later administrator customizations.
        for role_name, defaults in DEFAULT_ROLE_PERMISSIONS.items():
            role_id = role_id_by_name.get(role_name)
            if role_id is None:
                continue
            for key in defaults - existing_permission_keys:
                self._db_execute(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_key) VALUES (?, ?)",
                    (role_id, key),
                )

    def _backfill_user_roles(self) -> None:
        """Backfill users.role_id defaults for any null role assignment."""

        role_id_by_name = self._role_id_by_name()
        default_user_role_id = role_id_by_name.get("user")
        if default_user_role_id is None:
            raise AuthError("Default user role missing.")
        self._db_execute(
            "UPDATE users SET role_id = ?, updated_at_ms = ? WHERE role_id IS NULL",
            (default_user_role_id, self.now_ms()),
        )

    def _role_id_by_name(self) -> dict[str, int]:
        """Return mapping of role name to role id."""

        rows = self._db_fetchall("SELECT id, name FROM roles")
        return {str(row["name"]): int(row["id"]) for row in rows}

    def _permissions_by_role_id(self) -> dict[int, set[str]]:
        """Return mapping from role id to assigned permission keys."""

        rows = self._db_fetchall("SELECT role_id, permission_key FROM role_permissions")
        permissions_by_role: dict[int, set[str]] = {}
        for row in rows:
            role_id = int(row["role_id"])
            permissions_by_role.setdefault(role_id, set()).add(
                str(row["permission_key"])
            )
        return permissions_by_role

    def _active_admin_count(self) -> int:
        """Return count of active users currently assigned admin role."""

        row = self._db_fetchone(
            """
            SELECT COUNT(*) AS c
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE r.name = 'admin' AND u.status = 'active'
            """
        )
        return int(row["c"]) if row is not None else 0

    def _create_session(self, user: AuthUser) -> AuthSession:
        """Issue and persist a new session token for a user."""

        token = secrets.token_urlsafe(48)
        token_hash = self._hash_token(token)
        now_ms = self.now_ms()
        expires_at_ms = now_ms + SESSION_TTL_MS
        self._db_execute(
            """
            INSERT INTO sessions (user_id, token_hash, created_at_ms, last_seen_at_ms, expires_at_ms, revoked_at_ms, ip, user_agent)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            (user.id, token_hash, now_ms, now_ms, expires_at_ms),
        )
        row = self._db_fetchone("SELECT last_insert_rowid() AS id")
        if row is None:
            raise AuthError("Failed to create session.")
        session_id = str(row["id"])
        self._db_commit()
        return AuthSession(session_id=session_id, token=token, user=user)

    def _get_user_by_username(self, username: str) -> AuthUser | None:
        """Fetch one user by normalized username."""

        row = self._db_fetchone(
            """
            SELECT
                u.id,
                u.username,
                r.name AS role_name,
                u.status,
                u.email,
                us.last_nickname,
                us.last_x,
                us.last_y,
                us.last_z,
                us.last_facing_deg
            FROM users u
            JOIN roles r ON r.id = u.role_id
            LEFT JOIN user_state us ON us.user_id = u.id
            WHERE u.username = ?
            """,
            (username,),
        )
        if row is None:
            return None
        return self._row_to_user(row)

    def _db_execute(self, sql: str, params: tuple | None = None) -> sqlite3.Cursor:
        """Run one SQL statement with a thread-safe connection lock."""

        with self._conn_lock:
            return self._conn.execute(sql, params or ())

    def _db_fetchone(self, sql: str, params: tuple | None = None) -> sqlite3.Row | None:
        """Run one query and fetch a single row with connection locking."""

        with self._conn_lock:
            return self._conn.execute(sql, params or ()).fetchone()

    def _db_fetchall(self, sql: str, params: tuple | None = None) -> list[sqlite3.Row]:
        """Run one query and fetch all rows with connection locking."""

        with self._conn_lock:
            return self._conn.execute(sql, params or ()).fetchall()

    def _db_commit(self) -> None:
        """Commit pending DB writes with connection locking."""

        with self._conn_lock:
            self._conn.commit()

    def _db_rollback(self) -> None:
        """Rollback pending DB writes with connection locking."""

        with self._conn_lock:
            self._conn.rollback()

    def _row_to_user(self, row: sqlite3.Row) -> AuthUser:
        """Convert a DB row into AuthUser."""

        user_id = str(row["id"])
        return AuthUser(
            id=user_id,
            username=str(row["username"]),
            role=str(row["role_name"]),
            permissions=tuple(sorted(self.get_user_permissions(user_id))),
            status=str(row["status"]),
            email=row["email"],
            last_nickname=row["last_nickname"]
            if "last_nickname" in row.keys()
            else None,
            last_x=row["last_x"] if "last_x" in row.keys() else None,
            last_y=row["last_y"] if "last_y" in row.keys() else None,
            last_z=row["last_z"] if "last_z" in row.keys() else None,
            last_facing_deg=row["last_facing_deg"]
            if "last_facing_deg" in row.keys()
            else None,
        )

    @staticmethod
    def _normalize_username(username: str) -> str:
        """Normalize username into canonical stored form."""

        return username.strip().lower()

    @staticmethod
    def _normalize_role_name(role_name: str) -> str:
        """Normalize role names to canonical lowercase identifiers."""

        return role_name.strip().lower()

    @staticmethod
    def _normalize_email(email: str | None) -> str | None:
        """Normalize optional email and collapse blanks to None."""

        if email is None:
            return None
        cleaned = email.strip().lower()
        return cleaned or None

    def _validate_username(self, username: str) -> None:
        """Validate username against length and character policy."""

        if not (self.username_min_length <= len(username) <= self.username_max_length):
            raise AuthError(
                f"Username must be between {self.username_min_length} and {self.username_max_length} characters."
            )
        if USERNAME_PATTERN.fullmatch(username) is None:
            raise AuthError(
                "Username may include lowercase letters, numbers, underscores, and dashes only."
            )

    @staticmethod
    def _validate_role_name(role_name: str) -> None:
        """Validate role name syntax and max length for custom-role creation."""

        if not role_name:
            raise AuthError("Role name is required.")
        if len(role_name) > 32:
            raise AuthError("Role name must be 32 characters or fewer.")
        if ROLE_NAME_PATTERN.fullmatch(role_name) is None:
            raise AuthError(
                "Role name may include lowercase letters, numbers, underscores, and dashes only."
            )

    @staticmethod
    def _validate_permission_keys(permission_keys: list[str]) -> set[str]:
        """Validate and normalize permission key sets for role updates."""

        validated: set[str] = set()
        for raw in permission_keys:
            key = str(raw).strip()
            if not key:
                continue
            if key not in PERMISSIONS:
                raise AuthError(f"Unknown permission: {key}")
            validated.add(key)
        return validated

    def _validate_password(self, password: str) -> None:
        """Validate password length policy."""

        if not (self.password_min_length <= len(password) <= self.password_max_length):
            raise AuthError(
                f"Password must be between {self.password_min_length} and {self.password_max_length} characters."
            )

    def _hash_password(self, password: str) -> str:
        """Hash a password using Argon2id."""

        return self._password_hasher.hash(password)

    def _verify_password(self, password: str, stored: str) -> bool:
        """Verify plaintext password against stored Argon2id hash."""

        try:
            return bool(self._password_hasher.verify(stored, password))
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            return False

    def _hash_token(self, token: str) -> str:
        """Hash a session token with server secret before persistence."""

        return hmac.new(
            self._token_secret, token.encode("utf-8"), hashlib.sha256
        ).hexdigest()
