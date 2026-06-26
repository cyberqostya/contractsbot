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
    signed_contract_file_id: str | None
    invoice_file_id: str | None
    invoice_text: str | None
    payment_status: str
    created_at: str


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
                    signed_contract_file_id TEXT,
                    invoice_file_id TEXT,
                    invoice_text TEXT,
                    payment_status TEXT NOT NULL DEFAULT 'waiting_files',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
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
            signed_contract_file_id=row["signed_contract_file_id"],
            invoice_file_id=row["invoice_file_id"],
            invoice_text=row["invoice_text"],
            payment_status=row["payment_status"],
            created_at=row["created_at"],
        )
