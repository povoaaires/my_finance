from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from dataclasses import dataclass, asdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
FIELDS = ("date", "amount", "description", "category", "payment_method", "installments", "invoice_month")


def today() -> date:
    return __import__("datetime").datetime.now(SAO_PAULO).date()


def folded(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c)).strip()


def canonical(value: str | None, choices: list[str]) -> str | None:
    if not value:
        return None
    return next((choice for choice in choices if folded(choice) == folded(value)), None)


def money(value: str | None) -> Decimal | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        return None
    return amount


@dataclass
class Expense:
    date: str | None = None
    amount: str | None = None
    description: str | None = None
    category: str | None = None
    payment_method: str | None = None
    installments: int | None = None
    invoice_month: str | None = None

    @classmethod
    def from_dict(cls, value: dict) -> "Expense":
        return cls(**{key: value.get(key) for key in FIELDS})

    def as_dict(self) -> dict:
        return asdict(self)

    def merge(self, patch: dict) -> "Expense":
        result = self.as_dict()
        for key in FIELDS:
            if patch.get(key) is not None:
                result[key] = patch[key]
        return Expense.from_dict(result)

    def validate(self, categories: list[str], methods: list[str]) -> tuple["Expense", list[str], list[str]]:
        result = Expense.from_dict(self.as_dict())
        missing: list[str] = []
        errors: list[str] = []
        try:
            if result.date:
                date.fromisoformat(result.date)
            else:
                missing.append("data")
        except ValueError:
            errors.append("Data inválida; informe dia, mês e ano.")
            result.date = None
        parsed_amount = money(result.amount)
        if parsed_amount is None:
            missing.append("valor")
            result.amount = None
        else:
            result.amount = format(parsed_amount, ".2f")
        result.description = (result.description or "").strip()
        if not result.description:
            missing.append("descrição")
            result.description = None
        elif len(result.description) > 180:
            errors.append("Descrição longa demais (máximo 180 caracteres).")
            result.description = None
        if not result.category:
            missing.append("categoria")
        else:
            category = canonical(result.category, categories)
            if category is None:
                errors.append("Categoria fora da lista de gastos da planilha.")
                result.category = None
            else:
                result.category = category
        if not result.payment_method:
            missing.append("meio de pagamento")
        else:
            method = canonical(result.payment_method, methods)
            if method is None:
                errors.append("Meio de pagamento fora da lista da planilha.")
                result.payment_method = None
            else:
                result.payment_method = method
        if result.installments is not None:
            if not isinstance(result.installments, int) or isinstance(result.installments, bool) or not 1 <= result.installments <= 120:
                errors.append("Parcelas devem ser um número de 1 a 120.")
                result.installments = None
        if result.payment_method == "Crédito":
            result.installments = result.installments or 1
        elif result.installments not in (None, 1):
            errors.append("Parcelamento só é aceito para Crédito.")
            result.installments = None
        else:
            result.installments = None
        if result.invoice_month:
            try:
                invoice = date.fromisoformat(result.invoice_month)
                if invoice.day != 1:
                    raise ValueError
            except ValueError:
                errors.append("Fatura manual deve ser o primeiro dia do mês (AAAA-MM-01).")
                result.invoice_month = None
        return result, missing, errors


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    telegram_user_id: int
    openai_key: str
    workbook_dir: Path
    data_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        required = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID", "OPENAI_API_KEY")
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise RuntimeError("Variáveis ausentes: " + ", ".join(missing))
        data_dir = Path(os.environ.get("FINANCE_BOT_DATA_DIR", Path.home() / ".my_finance_bot"))
        data_dir.mkdir(parents=True, exist_ok=True)
        workbook_dir = Path(os.environ.get(
            "FINANCE_BOT_WORKBOOK_DIR", Path.home() / "OneDrive" / "Finanças"
        )).expanduser().resolve()
        if not workbook_dir.is_dir():
            raise RuntimeError(f"Pasta das planilhas não encontrada: {workbook_dir}")
        return cls(os.environ["TELEGRAM_BOT_TOKEN"], int(os.environ["TELEGRAM_USER_ID"]),
                   os.environ["OPENAI_API_KEY"], workbook_dir, data_dir)


class Store:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS local_workbooks (
                    year INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    categories TEXT NOT NULL, methods TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, version INTEGER NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, source_message_id INTEGER NOT NULL,
                    saved_row INTEGER
                );
                CREATE TABLE IF NOT EXISTS seen_messages (
                    chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
                    PRIMARY KEY(chat_id, message_id)
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def put_book(self, year: int, name: str, categories: list[str], methods: list[str]) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO local_workbooks VALUES (?, ?, ?, ?)",
                       (year, name, json.dumps(categories), json.dumps(methods)))

    def book(self, year: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM local_workbooks WHERE year = ?", (year,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["categories"] = json.loads(result["categories"])
        result["methods"] = json.loads(result["methods"])
        return result

    def all_books(self) -> list[dict]:
        with self.connect() as db:
            years = [row[0] for row in db.execute("SELECT year FROM local_workbooks ORDER BY year")]
        return [self.book(year) for year in years]

    def active(self, chat_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM drafts WHERE chat_id = ? AND status IN ('collecting','ready','saving','uncertain') ORDER BY rowid DESC LIMIT 1",
                (chat_id,)).fetchone()
        return dict(row) if row else None

    def draft(self, draft_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(row) if row else None

    def save_draft(self, draft_id: str, chat_id: int, version: int, status: str, expense: Expense, message_id: int) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO drafts(id,chat_id,version,status,payload,source_message_id) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET version=excluded.version,status=excluded.status,"
                "payload=excluded.payload,source_message_id=excluded.source_message_id",
                (draft_id, chat_id, version, status, json.dumps(expense.as_dict()), message_id))

    def transition(self, draft_id: str, version: int, old: str, new: str, saved_row: int | None = None) -> bool:
        with self.connect() as db:
            cur = db.execute("UPDATE drafts SET status=?, saved_row=COALESCE(?,saved_row) "
                             "WHERE id=? AND version=? AND status=?", (new, saved_row, draft_id, version, old))
            return cur.rowcount == 1

    def seen(self, chat_id: int, message_id: int) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM seen_messages WHERE chat_id=? AND message_id=?",
                              (chat_id, message_id)).fetchone() is not None

    def mark_seen(self, chat_id: int, message_id: int) -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO seen_messages VALUES(?,?)", (chat_id, message_id))
