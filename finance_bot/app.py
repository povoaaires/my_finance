from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dotenv import load_dotenv
import re
import uuid
from datetime import date
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .core import Expense, Settings, Store, today
from .integrations import ConflictError, append_expense, inspect_workbook
from .parser import ExpenseParser

log = logging.getLogger(__name__)


class BotService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.data_dir / "state.sqlite3")
        self.parser = ExpenseParser(settings.openai_key)
        with self.store.connect() as db:
            db.execute("UPDATE drafts SET status='uncertain' WHERE status='saving'")

    def allowed(self, update: Update) -> bool:
        return bool(update.effective_user and update.effective_chat
                    and update.effective_chat.type == "private"
                    and update.effective_user.id == self.settings.telegram_user_id)

    def choices(self) -> tuple[list[str], list[str]]:
        books = self.store.all_books()
        return (sorted({v for b in books for v in b["categories"]}),
                sorted({v for b in books for v in b["methods"]}))

    def validate(self, draft: Expense) -> tuple[Expense, list[str], list[str], dict | None]:
        book = None
        if draft.date:
            try:
                book = self.store.book(date.fromisoformat(draft.date).year)
            except ValueError:
                pass
        categories, methods = (book["categories"], book["methods"]) if book else self.choices()
        checked, missing, errors = draft.validate(categories, methods)
        return checked, missing, errors, book

    def book_path(self, year: int) -> Path:
        path = (self.settings.workbook_dir / f"{year}.xlsx").resolve(strict=True)
        if path.parent != self.settings.workbook_dir or not path.is_file():
            raise ValueError("Planilha fora da pasta configurada.")
        return path

    def commit(self, expense: Expense, book: dict) -> int:
        year = date.fromisoformat(expense.date).year
        if book["year"] != year or book["name"] != f"{year}.xlsx":
            raise ValueError("O vínculo do ano não corresponde à planilha.")
        path = self.book_path(year)
        with path.open("rb") as file:
            original_hash = hashlib.file_digest(file, "sha256").digest()
        categories, methods = inspect_workbook(path)
        _, missing, errors = expense.validate(categories, methods)
        if missing or errors:
            raise ValueError("As opções da planilha mudaram. Revise o lançamento.")
        with path.open("rb") as file:
            current_hash = hashlib.file_digest(file, "sha256").digest()
        if current_hash != original_hash:
            raise ConflictError("A planilha local mudou durante a validação.")
        return append_expense(path, expense)


def confirmation(expense: Expense, draft_id: str, version: int):
    amount = f"{float(expense.amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    lines = [
        "Confirme o gasto:",
        f"Data: {date.fromisoformat(expense.date):%d/%m/%Y}",
        f"Valor: R$ {amount}",
        f"Descrição: {expense.description}",
        f"Categoria: {expense.category}",
        f"Meio: {expense.payment_method}",
    ]
    if expense.payment_method == "Crédito":
        lines.append(f"Parcelas: {expense.installments}")
    if expense.invoice_month:
        lines.append(f"Fatura manual: {date.fromisoformat(expense.invoice_month):%m/%Y}")
    lines.append(f"Arquivo: {expense.date[:4]}.xlsx")
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("Salvar", callback_data=f"save:{draft_id}:{version}"),
        InlineKeyboardButton("Cancelar", callback_data=f"cancel:{draft_id}:{version}"),
    ]])
    return "\n".join(lines), buttons


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = context.application.bot_data["svc"]
    if svc.allowed(update):
        await update.message.reply_text(
            "Envie uma despesa por mensagem com data, valor, descrição, categoria e meio de pagamento.\n"
            "Exemplo: 27/09/2026 mercado R$ 54,90, Alimentação, Pix.\n"
            "Comandos: /vincular_ano ANO, /status, /cancelar, /ajuda."
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = context.application.bot_data["svc"]
    if not svc.allowed(update):
        return
    years = ", ".join(str(b["year"]) for b in svc.store.all_books()) or "nenhum"
    active = svc.store.active(update.effective_chat.id)
    await update.message.reply_text(f"Anos vinculados: {years}.\nRascunho: {active['status'] if active else 'nenhum'}.")


async def bind_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = context.application.bot_data["svc"]
    if not svc.allowed(update):
        return
    args = context.args or []
    if len(args) != 1 or not re.fullmatch(r"20\d{2}", args[0]):
        await update.message.reply_text("Use /vincular_ano 2026")
        return
    year = int(args[0])
    try:
        path = svc.book_path(year)
        categories, methods = await asyncio.to_thread(inspect_workbook, path)
        svc.store.put_book(year, path.name, categories, methods)
    except Exception:
        log.exception("Falha ao vincular planilha")
        await update.message.reply_text(
            f"Não consegui vincular {year}.xlsx. Confira se está em "
            f"{svc.settings.workbook_dir} e se abre no Excel."
        )
        return
    await update.message.reply_text(
        f"{year} vinculado a {path.name}. "
        f"Encontrei {len(categories)} categorias de gasto e {len(methods)} meios de pagamento."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = context.application.bot_data["svc"]
    if not svc.allowed(update):
        return
    active = svc.store.active(update.effective_chat.id)
    if not active:
        await update.message.reply_text("Não há lançamento pendente.")
    elif active["status"] == "saving":
        await update.message.reply_text("A gravação está em andamento. Aguarde o resultado.")
    else:
        svc.store.transition(active["id"], active["version"], active["status"], "cancelled")
        await update.message.reply_text("Rascunho cancelado.")


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = context.application.bot_data["svc"]
    if not svc.allowed(update) or not update.message or not update.message.text:
        return
    chat_id, message_id = update.effective_chat.id, update.message.message_id
    if svc.store.seen(chat_id, message_id):
        return
    if not svc.store.all_books():
        await update.message.reply_text("Vincule primeiro uma planilha com /vincular_ano 2026.")
        svc.store.mark_seen(chat_id, message_id)
        return
    active = svc.store.active(chat_id)
    if active and active["status"] in ("saving", "uncertain"):
        await update.message.reply_text(
            "Há uma gravação em andamento ou com resultado incerto. Confira a planilha e use /cancelar para continuar."
        )
        svc.store.mark_seen(chat_id, message_id)
        return
    previous = Expense.from_dict(json.loads(active["payload"])) if active else Expense()
    categories, methods = svc.choices()
    try:
        patch = await asyncio.to_thread(
            svc.parser.parse, update.message.text, previous, categories, methods, today()
        )
        if patch.pop("multiple_expenses"):
            await update.message.reply_text("Envie uma despesa por mensagem.")
            svc.store.mark_seen(chat_id, message_id)
            return
        expense, missing, errors, book = svc.validate(previous.merge(patch))
    except Exception:
        log.exception("Falha na interpretação")
        await update.message.reply_text("Não consegui interpretar a mensagem. Tente novamente.")
        return
    draft_id = active["id"] if active else uuid.uuid4().hex
    version = active["version"] + 1 if active else 1
    state = "ready" if not missing and not errors and book else "collecting"
    svc.store.save_draft(draft_id, chat_id, version, state, expense, message_id)
    if errors:
        await update.message.reply_text("\n".join(errors))
    if missing:
        await update.message.reply_text("Faltam: " + ", ".join(missing) + ". Envie os dados faltantes.")
    elif errors:
        pass
    elif book is None:
        await update.message.reply_text(
            f"Não há planilha vinculada para {expense.date[:4]}. Use /vincular_ano {expense.date[:4]}."
        )
    elif not errors:
        text, buttons = confirmation(expense, draft_id, version)
        await update.message.reply_text(text + "\nPara corrigir, envie os novos dados.", reply_markup=buttons)
    svc.store.mark_seen(chat_id, message_id)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = context.application.bot_data["svc"]
    if not svc.allowed(update) or not update.callback_query:
        return
    query = update.callback_query
    await query.answer()
    try:
        action, draft_id, raw_version = query.data.split(":")
        version = int(raw_version)
    except (AttributeError, ValueError):
        return
    draft = svc.store.draft(draft_id)
    if not draft or draft["chat_id"] != update.effective_chat.id or draft["version"] != version or draft["status"] != "ready":
        await query.message.reply_text("Essa confirmação expirou.")
        return
    if action == "cancel":
        if svc.store.transition(draft_id, version, "ready", "cancelled"):
            await query.message.reply_text("Lançamento cancelado.")
        return
    if action != "save":
        return
    expense = Expense.from_dict(json.loads(draft["payload"]))
    book = svc.store.book(date.fromisoformat(expense.date).year)
    if not book:
        await query.message.reply_text("Planilha do ano não vinculada.")
        return
    if not svc.store.transition(draft_id, version, "ready", "saving"):
        await query.message.reply_text("Essa confirmação já foi usada.")
        return
    try:
        row = await asyncio.to_thread(svc.commit, expense, book)
    except ConflictError as exc:
        svc.store.transition(draft_id, version, "saving", "ready")
        await query.message.reply_text(f"{exc} Confira e toque em Salvar novamente.")
        return
    except Exception:
        log.exception("Falha na gravação")
        svc.store.transition(draft_id, version, "saving", "uncertain")
        await query.message.reply_text(
            "Não consegui confirmar a gravação. Confira a planilha local antes de tentar novamente; "
            "use /cancelar depois de verificar."
        )
        return
    svc.store.transition(draft_id, version, "saving", "saved", saved_row=row)
    await query.message.reply_text(f"Despesa salva localmente em {book['name']}, linha {row}. Confira a sincronização do OneDrive.")


def main() -> None:
    import sys
    logging.basicConfig(level=logging.INFO)
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command == "id":
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("Defina TELEGRAM_BOT_TOKEN.")
        import requests
        response = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"limit": 20, "timeout": 0}, timeout=15,
        )
        response.raise_for_status()
        ids = {
            item["message"]["from"]["id"]
            for item in response.json().get("result", [])
            if item.get("message", {}).get("chat", {}).get("type") == "private"
        }
        print("IDs de conversas privadas recentes:", ", ".join(map(str, sorted(ids))) or "nenhum")
        return
    if command != "run":
        raise RuntimeError("Use finance-bot ou finance-bot id.")
    settings = Settings.from_env()
    svc = BotService(settings)
    app = ApplicationBuilder().token(settings.telegram_token).concurrent_updates(False).build()
    app.bot_data["svc"] = svc
    app.add_handler(CommandHandler(["start", "ajuda"], start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("vincular_ano", bind_year))
    app.add_handler(CommandHandler("cancelar", cancel))
    app.add_handler(CallbackQueryHandler(callback, pattern=r"^(save|cancel):[0-9a-f]{32}:\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text))
    app.run_polling()


if __name__ == "__main__":
    main()