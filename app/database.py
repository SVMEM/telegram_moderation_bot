from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .rules import Rule, WhitelistEntry


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

    def init_schema(self, owner_ids: set[int], default_mode: str) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS system_users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT,
                role TEXT NOT NULL DEFAULT 'moderator',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'connected',
                mode TEXT NOT NULL DEFAULT 'test',
                notify_enabled INTEGER NOT NULL DEFAULT 1,
                ml_enabled INTEGER NOT NULL DEFAULT 1,
                ml_threshold REAL NOT NULL DEFAULT 0.55,
                owner_id INTEGER,
                can_delete_messages INTEGER NOT NULL DEFAULT 0,
                connected_at TEXT NOT NULL,
                last_rights_check TEXT
            );

            CREATE TABLE IF NOT EXISTS user_chat_access (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, chat_id),
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                rule_type TEXT NOT NULL,
                pattern TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT 'delete',
                priority INTEGER NOT NULL DEFAULT 100,
                status TEXT NOT NULL DEFAULT 'active',
                created_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_rules_chat_status
                ON rules(chat_id, status, priority);

            CREATE TABLE IF NOT EXISTS whitelist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                entry_type TEXT NOT NULL,
                value TEXT NOT NULL,
                expires_at TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_whitelist_chat
                ON whitelist(chat_id, entry_type, value);

            CREATE TABLE IF NOT EXISTS moderation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER,
                author_id INTEGER,
                author_username TEXT,
                text_excerpt TEXT,
                rule_id INTEGER,
                matched TEXT,
                action TEXT,
                result TEXT NOT NULL,
                error TEXT,
                is_edited INTEGER NOT NULL DEFAULT 0,
                message_link TEXT,
                ml_score REAL,
                ml_model_version TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_events_chat_time
                ON moderation_events(chat_id, created_at);

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER,
                chat_id INTEGER,
                entity TEXT NOT NULL,
                entity_id TEXT,
                action TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'inactive',
                valid_until TEXT,
                last_payment_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                provider_invoice_id INTEGER NOT NULL UNIQUE,
                amount TEXT NOT NULL,
                fiat TEXT NOT NULL,
                status TEXT NOT NULL,
                invoice_url TEXT NOT NULL,
                raw_payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_payments_status
                ON payments(status, created_at);
            """
        )
        self._ensure_column("chats", "ml_enabled", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("chats", "ml_threshold", "REAL NOT NULL DEFAULT 0.55")
        self._ensure_column("moderation_events", "ml_score", "REAL")
        self._ensure_column("moderation_events", "ml_model_version", "TEXT")
        for owner_id in owner_ids:
            self.conn.execute(
                """
                INSERT INTO system_users(user_id, role, status, created_at, updated_at)
                VALUES (?, 'owner', 'active', ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    role='owner',
                    status='active',
                    updated_at=excluded.updated_at
                """,
                (owner_id, now_iso(), now_iso()),
            )
        self.conn.commit()
        self.default_mode = default_mode

    def _ensure_column(self, table: str, column: str, spec: str) -> None:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")

    def seed_settings(self, defaults: dict[str, str]) -> None:
        for key, value in defaults.items():
            self.conn.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, value, now_iso()),
            )
        self.conn.commit()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def get_bool_setting(self, key: str, default: bool = False) -> bool:
        value = self.get_setting(key)
        if value is None:
            return default
        return value.lower() in {"1", "true", "yes", "on"}

    def get_int_setting(self, key: str, default: int) -> int:
        value = self.get_setting(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def set_setting(self, key: str, value: str, actor_id: int | None = None) -> None:
        old_value = self.get_setting(key)
        self.conn.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now_iso()),
        )
        self.conn.commit()
        self.audit(
            actor_id=actor_id,
            chat_id=None,
            entity="setting",
            entity_id=key,
            action="set",
            old_value=old_value,
            new_value=value,
        )

    def audit(
        self,
        *,
        actor_id: int | None,
        chat_id: int | None,
        entity: str,
        entity_id: str | None,
        action: str,
        old_value: Any = None,
        new_value: Any = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO audit_log(actor_id, chat_id, entity, entity_id, action, old_value, new_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_id,
                chat_id,
                entity,
                entity_id,
                action,
                json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
                json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
                now_iso(),
            ),
        )
        self.conn.commit()

    def ensure_user(
        self,
        user_id: int,
        full_name: str | None,
        username: str | None,
        role: str = "moderator",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO system_users(user_id, full_name, username, role, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                full_name=excluded.full_name,
                username=excluded.username,
                updated_at=excluded.updated_at
            """,
            (user_id, full_name, username, role, now_iso(), now_iso()),
        )
        self.conn.commit()

    def set_global_role(self, user_id: int, role: str) -> None:
        self.conn.execute(
            """
            INSERT INTO system_users(user_id, role, status, created_at, updated_at)
            VALUES (?, ?, 'active', ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET role=excluded.role, status='active', updated_at=excluded.updated_at
            """,
            (user_id, role, now_iso(), now_iso()),
        )
        self.conn.commit()

    def get_global_role(self, user_id: int | None) -> str | None:
        if user_id is None:
            return None
        row = self.conn.execute(
            "SELECT role FROM system_users WHERE user_id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()
        return row["role"] if row else None

    def is_owner(self, user_id: int | None) -> bool:
        return self.get_global_role(user_id) == "owner"

    def can_manage_chat(self, user_id: int | None, chat_id: int | None = None) -> bool:
        role = self.get_global_role(user_id)
        if role in {"owner", "admin"}:
            return True
        if user_id is None or chat_id is None:
            return False
        row = self.conn.execute(
            """
            SELECT role FROM user_chat_access
            WHERE user_id = ? AND chat_id = ? AND role IN ('admin', 'moderator')
            """,
            (user_id, chat_id),
        ).fetchone()
        return row is not None

    def grant_chat_access(self, user_id: int, chat_id: int, role: str = "admin") -> None:
        self.conn.execute(
            """
            INSERT INTO user_chat_access(user_id, chat_id, role, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET role=excluded.role
            """,
            (user_id, chat_id, role, now_iso()),
        )
        self.conn.commit()

    def revoke_chat_access(self, user_id: int, chat_id: int) -> None:
        self.conn.execute(
            "DELETE FROM user_chat_access WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        self.conn.commit()

    def upsert_chat(
        self,
        *,
        chat_id: int,
        title: str,
        chat_type: str,
        owner_id: int | None,
        can_delete_messages: bool,
        status: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO chats(
                chat_id, title, type, status, mode, notify_enabled, ml_enabled, ml_threshold, owner_id,
                can_delete_messages, connected_at, last_rights_check
            )
            VALUES (?, ?, ?, ?, ?, 1, 1, 0.55, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title=excluded.title,
                type=excluded.type,
                status=excluded.status,
                owner_id=COALESCE(chats.owner_id, excluded.owner_id),
                can_delete_messages=excluded.can_delete_messages,
                last_rights_check=excluded.last_rights_check
            """,
            (
                chat_id,
                title,
                chat_type,
                status,
                getattr(self, "default_mode", "test"),
                owner_id,
                1 if can_delete_messages else 0,
                now_iso(),
                now_iso(),
            ),
        )
        self.conn.commit()

    def get_chat(self, chat_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()

    def list_chats(self, user_id: int | None) -> list[sqlite3.Row]:
        role = self.get_global_role(user_id)
        if role in {"owner", "admin"}:
            rows = self.conn.execute("SELECT * FROM chats ORDER BY title").fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT chats.* FROM chats
                JOIN user_chat_access ON user_chat_access.chat_id = chats.chat_id
                WHERE user_chat_access.user_id = ?
                ORDER BY chats.title
                """,
                (user_id,),
            ).fetchall()
        return list(rows)

    def set_mode(self, chat_id: int, mode: str, actor_id: int | None) -> None:
        old = self.get_chat(chat_id)
        self.conn.execute("UPDATE chats SET mode = ? WHERE chat_id = ?", (mode, chat_id))
        self.conn.commit()
        self.audit(
            actor_id=actor_id,
            chat_id=chat_id,
            entity="chat",
            entity_id=str(chat_id),
            action="set_mode",
            old_value=dict(old) if old else None,
            new_value={"mode": mode},
        )

    def set_ml(self, chat_id: int, enabled: bool, threshold: float, actor_id: int | None) -> None:
        old = self.get_chat(chat_id)
        self.conn.execute(
            "UPDATE chats SET ml_enabled = ?, ml_threshold = ? WHERE chat_id = ?",
            (1 if enabled else 0, threshold, chat_id),
        )
        self.conn.commit()
        self.audit(
            actor_id=actor_id,
            chat_id=chat_id,
            entity="chat",
            entity_id=str(chat_id),
            action="set_ml",
            old_value=dict(old) if old else None,
            new_value={"ml_enabled": enabled, "ml_threshold": threshold},
        )

    def add_rule(
        self,
        *,
        chat_id: int,
        name: str,
        rule_type: str,
        pattern: str,
        action: str,
        priority: int,
        created_by: int | None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO rules(chat_id, name, rule_type, pattern, action, priority, status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (chat_id, name, rule_type, pattern, action, priority, created_by, now_iso(), now_iso()),
        )
        self.conn.commit()
        rule_id = int(cursor.lastrowid)
        self.audit(
            actor_id=created_by,
            chat_id=chat_id,
            entity="rule",
            entity_id=str(rule_id),
            action="add",
            new_value={
                "name": name,
                "rule_type": rule_type,
                "pattern": pattern,
                "action": action,
                "priority": priority,
            },
        )
        return rule_id

    def get_rule(self, rule_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()

    def delete_rule(self, rule_id: int, actor_id: int | None) -> bool:
        old = self.get_rule(rule_id)
        if not old:
            return False
        self.conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        self.conn.commit()
        self.audit(
            actor_id=actor_id,
            chat_id=old["chat_id"],
            entity="rule",
            entity_id=str(rule_id),
            action="delete",
            old_value=dict(old),
        )
        return True

    def list_rules(self, chat_id: int, active_only: bool = False) -> list[Rule]:
        sql = "SELECT * FROM rules WHERE chat_id = ?"
        params: tuple[Any, ...] = (chat_id,)
        if active_only:
            sql += " AND status = 'active'"
        sql += " ORDER BY priority DESC, id"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            Rule(
                id=row["id"],
                chat_id=row["chat_id"],
                name=row["name"],
                rule_type=row["rule_type"],
                pattern=row["pattern"],
                action=row["action"],
                priority=row["priority"],
                status=row["status"],
            )
            for row in rows
        ]

    def add_whitelist(
        self,
        *,
        chat_id: int,
        entry_type: str,
        value: str,
        created_by: int | None,
        expires_at: str | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO whitelist(chat_id, entry_type, value, expires_at, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, entry_type, value, expires_at, created_by, now_iso()),
        )
        self.conn.commit()
        entry_id = int(cursor.lastrowid)
        self.audit(
            actor_id=created_by,
            chat_id=chat_id,
            entity="whitelist",
            entity_id=str(entry_id),
            action="add",
            new_value={"entry_type": entry_type, "value": value, "expires_at": expires_at},
        )
        return entry_id

    def get_whitelist_entry(self, entry_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM whitelist WHERE id = ?", (entry_id,)).fetchone()

    def delete_whitelist(self, entry_id: int, actor_id: int | None) -> bool:
        old = self.get_whitelist_entry(entry_id)
        if not old:
            return False
        self.conn.execute("DELETE FROM whitelist WHERE id = ?", (entry_id,))
        self.conn.commit()
        self.audit(
            actor_id=actor_id,
            chat_id=old["chat_id"],
            entity="whitelist",
            entity_id=str(entry_id),
            action="delete",
            old_value=dict(old),
        )
        return True

    def list_whitelist(self, chat_id: int) -> list[WhitelistEntry]:
        rows = self.conn.execute(
            "SELECT * FROM whitelist WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
        return [
            WhitelistEntry(
                id=row["id"],
                chat_id=row["chat_id"],
                entry_type=row["entry_type"],
                value=row["value"],
                expires_at=row["expires_at"],
            )
            for row in rows
        ]

    def add_event(
        self,
        *,
        chat_id: int,
        message_id: int | None,
        author_id: int | None,
        author_username: str | None,
        text_excerpt: str | None,
        rule_id: int | None,
        matched: str | None,
        action: str | None,
        result: str,
        error: str | None,
        is_edited: bool,
        message_link: str | None,
        ml_score: float | None = None,
        ml_model_version: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO moderation_events(
                chat_id, message_id, author_id, author_username, text_excerpt, rule_id,
                matched, action, result, error, is_edited, message_link, ml_score, ml_model_version, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                message_id,
                author_id,
                author_username,
                text_excerpt,
                rule_id,
                matched,
                action,
                result,
                error,
                1 if is_edited else 0,
                message_link,
                ml_score,
                ml_model_version,
                now_iso(),
            ),
        )
        self.conn.commit()

    def list_events(self, chat_id: int | None, limit: int = 20) -> list[sqlite3.Row]:
        limit = max(1, min(limit, 100))
        if chat_id is None:
            rows = self.conn.execute(
                "SELECT * FROM moderation_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM moderation_events WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        return list(rows)

    def stats(self, chat_id: int | None = None) -> dict[str, int]:
        where = ""
        params: tuple[Any, ...] = ()
        if chat_id is not None:
            where = "WHERE chat_id = ?"
            params = (chat_id,)

        def count(extra_where: str = "") -> int:
            clause = where
            extra_params = params
            if extra_where:
                clause = f"{where} AND {extra_where}" if where else f"WHERE {extra_where}"
            row = self.conn.execute(
                f"SELECT COUNT(*) AS count FROM moderation_events {clause}",
                extra_params,
            ).fetchone()
            return int(row["count"])

        return {
            "events": count(),
            "deleted": count("result = 'deleted'"),
            "test_hits": count("result = 'test_hit'"),
            "errors": count("result = 'error'"),
            "ml_hits": count("ml_score IS NOT NULL"),
        }

    def prune_events(self, retention_days: int | None) -> None:
        if retention_days is None:
            return
        threshold = datetime.now(timezone.utc) - timedelta(days=retention_days)
        self.conn.execute(
            "DELETE FROM moderation_events WHERE created_at < ?",
            (threshold.isoformat(timespec="seconds"),),
        )
        self.conn.commit()

    def create_payment(
        self,
        *,
        user_id: int,
        provider_invoice_id: int,
        amount: str,
        fiat: str,
        invoice_url: str,
        raw_payload: Any | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO payments(user_id, provider, provider_invoice_id, amount, fiat, status, invoice_url, raw_payload, created_at, updated_at)
            VALUES (?, 'crypto_pay', ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                user_id,
                provider_invoice_id,
                amount,
                fiat,
                invoice_url,
                json.dumps(raw_payload, ensure_ascii=False) if raw_payload is not None else None,
                now_iso(),
                now_iso(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_pending_payments(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM payments WHERE status IN ('pending', 'active') ORDER BY created_at"
            ).fetchall()
        )

    def update_payment_status(self, *, payment_id: int, status: str, raw_payload: Any | None = None) -> None:
        self.conn.execute(
            """
            UPDATE payments
            SET status = ?, raw_payload = COALESCE(?, raw_payload), updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                json.dumps(raw_payload, ensure_ascii=False) if raw_payload is not None else None,
                now_iso(),
                payment_id,
            ),
        )
        self.conn.commit()

    def get_payment_by_invoice(self, provider_invoice_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM payments WHERE provider_invoice_id = ?",
            (provider_invoice_id,),
        ).fetchone()

    def get_latest_payment(self, user_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM payments WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    def activate_subscription(self, *, user_id: int, valid_until: str, payment_id: int | None) -> None:
        self.conn.execute(
            """
            INSERT INTO subscriptions(user_id, status, valid_until, last_payment_id, created_at, updated_at)
            VALUES (?, 'active', ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                status='active',
                valid_until=excluded.valid_until,
                last_payment_id=excluded.last_payment_id,
                updated_at=excluded.updated_at
            """,
            (user_id, valid_until, payment_id, now_iso(), now_iso()),
        )
        self.conn.commit()

    def get_subscription(self, user_id: int | None) -> sqlite3.Row | None:
        if user_id is None:
            return None
        return self.conn.execute("SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()

    def get_subscription_until(self, user_id: int) -> str | None:
        row = self.get_subscription(user_id)
        return row["valid_until"] if row else None

    def subscription_active(self, user_id: int | None) -> bool:
        row = self.get_subscription(user_id)
        if not row or row["status"] != "active" or not row["valid_until"]:
            return False
        try:
            valid_until = datetime.fromisoformat(row["valid_until"])
        except ValueError:
            return False
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        return valid_until > datetime.now(timezone.utc)
