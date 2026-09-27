from types import SimpleNamespace

import pytest

from finance_bot import app
from finance_bot.core import Expense, Settings
from finance_bot.integrations import ConflictError


def test_settings_only_requires_bot_credentials(monkeypatch, tmp_path):
    for key, value in {
        "TELEGRAM_BOT_TOKEN": "token",
        "TELEGRAM_USER_ID": "42",
        "OPENAI_API_KEY": "api-key",
        "FINANCE_BOT_WORKBOOK_DIR": str(tmp_path),
        "FINANCE_BOT_DATA_DIR": str(tmp_path / "data"),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MS_CLIENT_ID", raising=False)
    settings = Settings.from_env()
    assert settings.workbook_dir == tmp_path.resolve()


def test_commit_uses_local_workbook(monkeypatch, tmp_path):
    path = tmp_path / "2026.xlsx"
    path.write_bytes(b"original")
    svc = app.BotService.__new__(app.BotService)
    svc.settings = SimpleNamespace(workbook_dir=tmp_path.resolve())
    expense = Expense("2026-09-27", "10.00", "Mercado", "Outros", "Pix")
    book = {"year": 2026, "name": "2026.xlsx"}
    monkeypatch.setattr(app, "inspect_workbook", lambda local: (["Outros"], ["Pix"]))
    calls = []
    monkeypatch.setattr(app, "append_expense", lambda local, item: calls.append((local, item)) or 49)
    assert svc.commit(expense, book) == 49
    assert calls == [(path, expense)]


def test_commit_stops_if_local_file_changes_during_validation(monkeypatch, tmp_path):
    path = tmp_path / "2026.xlsx"
    path.write_bytes(b"original")
    svc = app.BotService.__new__(app.BotService)
    svc.settings = SimpleNamespace(workbook_dir=tmp_path.resolve())
    expense = Expense("2026-09-27", "10.00", "Mercado", "Outros", "Pix")

    def change_file(local):
        local.write_bytes(b"edited elsewhere")
        return ["Outros"], ["Pix"]

    monkeypatch.setattr(app, "inspect_workbook", change_file)
    monkeypatch.setattr(app, "append_expense", lambda *args: pytest.fail("Não deveria gravar"))
    with pytest.raises(ConflictError):
        svc.commit(expense, {"year": 2026, "name": "2026.xlsx"})
