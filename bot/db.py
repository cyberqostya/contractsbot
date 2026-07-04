from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Application:
    id: int
    user_id: int
    username: str | None
    amount: str
    status: str
    requisites: dict[str, str]
    generated_contract_path: str | None
    generated_contract_sha256: str | None
    signed_contract_file_id: str | None
    invoice_file_id: str | None
    invoice_text: str | None
    payment_status: str
    created_at: str


@dataclass(frozen=True)
class PersonalLink:
    token: str
    amount: str
    template_files: list[str]
    product_links: list[str]
    created_at: str
    expires_at: str
    used_at: str | None
    used_by_user_id: int | None
    application_id: int | None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    amount TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requisites_json TEXT NOT NULL,
                    generated_contract_path TEXT,
                    generated_contract_sha256 TEXT,
                    signed_contract_file_id TEXT,
                    invoice_file_id TEXT,
                    invoice_text TEXT,
                    payment_status TEXT NOT NULL DEFAULT 'waiting_files',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._ensure_column(conn, "applications", "generated_contract_sha256", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS personal_links (
                    token TEXT PRIMARY KEY,
                    amount TEXT NOT NULL,
                    template_files_json TEXT NOT NULL DEFAULT '[]',
                    product_links_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    used_by_user_id INTEGER,
                    application_id INTEGER
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_personal_links_expires_at ON personal_links(expires_at)"
            )
            conn.commit()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def delete_expired_links(self, now_iso: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM personal_links WHERE expires_at < ?",
                (now_iso,),
            )
            conn.commit()

    def create_personal_link(
        self,
        *,
        token: str,
        amount: str,
        template_files: list[str],
        product_links: list[str],
        created_at: str,
        expires_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO personal_links (
                    token, amount, template_files_json, product_links_json,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    amount,
                    json.dumps(template_files, ensure_ascii=False),
                    json.dumps(product_links, ensure_ascii=False),
                    created_at,
                    expires_at,
                ),
            )
            conn.commit()

    def get_personal_link(self, token: str) -> PersonalLink | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM personal_links WHERE token = ?",
                (token,),
            ).fetchone()
        return self._row_to_personal_link(row) if row else None

    def get_personal_link_by_application_id(self, application_id: int) -> PersonalLink | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM personal_links WHERE application_id = ?",
                (application_id,),
            ).fetchone()
        return self._row_to_personal_link(row) if row else None

    def mark_personal_link_used(
        self,
        *,
        token: str,
        user_id: int,
        application_id: int | None,
        used_at: str,
    ) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE personal_links
                SET used_at = ?, used_by_user_id = ?, application_id = ?
                WHERE token = ? AND used_at IS NULL AND expires_at >= ?
                """,
                (used_at, user_id, application_id, token, used_at),
            )
            conn.commit()
            return cursor.rowcount == 1

    def attach_application_to_link(self, token: str, application_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE personal_links SET application_id = ? WHERE token = ?",
                (application_id, token),
            )
            conn.commit()

    def create_application(
        self,
        *,
        user_id: int,
        username: str | None,
        amount: str,
        status: str,
        requisites: dict[str, str],
        generated_contract_path: str,
        created_at: str,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO applications (
                    user_id, username, amount, status, requisites_json,
                    generated_contract_path, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    amount,
                    status,
                    json.dumps(requisites, ensure_ascii=False),
                    generated_contract_path,
                    created_at,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def update_files(
        self,
        application_id: int,
        *,
        signed_contract_file_id: str | None = None,
        invoice_file_id: str | None = None,
        invoice_text: str | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        if signed_contract_file_id is not None:
            assignments.append("signed_contract_file_id = ?")
            values.append(signed_contract_file_id)
        if invoice_file_id is not None:
            assignments.append("invoice_file_id = ?")
            values.append(invoice_file_id)
        if invoice_text is not None:
            assignments.append("invoice_text = ?")
            values.append(invoice_text)
        if not assignments:
            return

        values.append(application_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE applications SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            conn.commit()

    def update_generated_contract_path(self, application_id: int, path: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE applications SET generated_contract_path = ? WHERE id = ?",
                (path, application_id),
            )
            conn.commit()

    def update_generated_contract_hash(self, application_id: int, sha256: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE applications SET generated_contract_sha256 = ? WHERE id = ?",
                (sha256, application_id),
            )
            conn.commit()

    def set_status(self, application_id: int, payment_status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE applications SET payment_status = ? WHERE id = ?",
                (payment_status, application_id),
            )
            conn.commit()

    def get_application(self, application_id: int) -> Application | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE id = ?",
                (application_id,),
            ).fetchone()
        return self._row_to_application(row) if row else None

    def latest_for_user(self, user_id: int) -> Application | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM applications
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return self._row_to_application(row) if row else None

    def latest_with_payment_status_for_user(
        self,
        user_id: int,
        payment_status: str,
    ) -> Application | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM applications
                WHERE user_id = ?
                  AND payment_status = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id, payment_status),
            ).fetchone()
        return self._row_to_application(row) if row else None

    @staticmethod
    def _row_to_application(row: sqlite3.Row) -> Application:
        return Application(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            username=row["username"],
            amount=row["amount"],
            status=row["status"],
            requisites=json.loads(row["requisites_json"]),
            generated_contract_path=row["generated_contract_path"],
            generated_contract_sha256=row["generated_contract_sha256"],
            signed_contract_file_id=row["signed_contract_file_id"],
            invoice_file_id=row["invoice_file_id"],
            invoice_text=row["invoice_text"],
            payment_status=row["payment_status"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_personal_link(row: sqlite3.Row) -> PersonalLink:
        return PersonalLink(
            token=row["token"],
            amount=row["amount"],
            template_files=json.loads(row["template_files_json"]),
            product_links=json.loads(row["product_links_json"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            used_at=row["used_at"],
            used_by_user_id=row["used_by_user_id"],
            application_id=row["application_id"],
        )
