from decimal import Decimal

from finance_bot.core import Expense, Store, money


def test_expense_requires_all_core_fields():
    expense, missing, errors = Expense(description="Mercado", amount="54,90").validate(
        ["Alimentação"], ["Pix"]
    )
    assert missing == ["data", "categoria", "meio de pagamento"]
    assert errors == []
    assert expense.amount == "54.90"


def test_credit_installments_and_category_normalization():
    expense = Expense("2026-09-27", "1.234,50", "Compra", "alimentacao", "credito", 3)
    checked, missing, errors = expense.validate(["Alimentação"], ["Crédito", "Pix"])
    assert not missing and not errors
    assert checked.category == "Alimentação"
    assert checked.payment_method == "Crédito"
    assert checked.installments == 3
    assert money(checked.amount) == Decimal("1234.50")


def test_invalid_category_and_non_credit_installments():
    expense = Expense("2026-09-27", "10", "Compra", "Inexistente", "Pix", 3)
    checked, missing, errors = expense.validate(["Outros"], ["Pix"])
    assert "Categoria fora da lista de gastos da planilha." in errors
    assert "Parcelamento só é aceito para Crédito." in errors
    assert checked.category is None


def test_store_year_lookup_and_confirmation_is_single_use(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    store.put_book(2026, "2026.xlsx", ["Outros"], ["Pix"])
    store.put_book(2027, "2027.xlsx", ["Outros"], ["Pix"])
    assert store.book(2026)["name"] == "2026.xlsx"
    assert store.book(2027)["name"] == "2027.xlsx"
    expense = Expense("2026-09-27", "10.00", "Compra", "Outros", "Pix")
    store.save_draft("abc", 42, 1, "ready", expense, 10)
    assert store.transition("abc", 1, "ready", "saving")
    assert not store.transition("abc", 1, "ready", "saving")
    assert store.transition("abc", 1, "saving", "saved", saved_row=49)
    assert store.draft("abc")["saved_row"] == 49
    store.mark_seen(42, 10)
    assert store.seen(42, 10)
