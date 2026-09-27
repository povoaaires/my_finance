from __future__ import annotations

from datetime import datetime
from pathlib import Path
import pythoncom
import win32com.client

from .core import Expense

HEADERS = [
    "Data", "Valor", "Descrição", "Operação", "Tipo", "Meio de pagamento",
    "Parcelas", "Fatura (manual)", "Mês", "Fatura", "Última parcela",
    "Valor da parcela", "Efeito na conta", "Efeito no ticket",
]


class ConflictError(RuntimeError):
    pass


def _open_excel(path: Path, readonly: bool):
    pythoncom.CoInitialize()
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.AskToUpdateLinks = False
    excel.AutomationSecurity = 3
    try:
        workbook = excel.Workbooks.Open(str(path.resolve()), UpdateLinks=0, ReadOnly=readonly)
        return excel, workbook
    except Exception:
        excel.Quit()

        raise


def _close_excel(excel, workbook) -> None:
    try:
        workbook.Close(SaveChanges=False)
    finally:
        excel.Quit()



def _table(workbook):
    sheet = workbook.Worksheets("Lançamentos")
    table = sheet.ListObjects("tblLanc")
    names = [str(table.HeaderRowRange.Cells(1, col).Value) for col in range(1, 15)]
    if names != HEADERS:
        raise ValueError("A tabela tblLanc não tem as 14 colunas esperadas.")
    return table


def _inspect_workbook_local(path: Path) -> tuple[list[str], list[str]]:
    excel, workbook = _open_excel(path, readonly=True)
    try:
        _table(workbook)
        config = workbook.Worksheets("Config")
        categories = []
        methods = []
        for row in range(5, 41):
            category = config.Cells(row, 5).Value
            group = config.Cells(row, 6).Value
            method = config.Cells(row, 7).Value
            if category and str(group).strip() == "Gasto":
                categories.append(str(category).strip())
            if method:
                methods.append(str(method).strip())
        if not categories or not methods:
            raise ValueError("A aba Config não contém categorias de gastos ou meios de pagamento.")
        return categories, methods
    finally:
        _close_excel(excel, workbook)


def _append_expense_local(path: Path, expense: Expense) -> int:
    excel, workbook = _open_excel(path, readonly=False)
    try:
        if workbook.ReadOnly:
            raise RuntimeError("O Excel abriu a planilha somente para leitura.")
        table = _table(workbook)
        target = None
        for index in range(1, table.ListRows.Count + 1):
            row = table.ListRows(index).Range
            if all(row.Cells(1, col).Value in (None, "") for col in range(2, 7)):
                target = row
                break
        if target is None:
            target = table.ListRows.Add().Range
        row_number = int(target.Row)
        values = [
            datetime.fromisoformat(expense.date), float(expense.amount),
            expense.description, "Gasto", expense.category, expense.payment_method,
            expense.installments if expense.payment_method == "Crédito" else None,
            datetime.fromisoformat(expense.invoice_month) if expense.invoice_month else None,
        ]
        target.Cells(1, 3).NumberFormat = "@"
        if values[2].startswith(("=", "+", "-", "@")):
            values[2] = "'" + values[2]
        for col, value in enumerate(values, start=1):
            target.Cells(1, col).Value = value
        for col in range(9, 15):
            cell = target.Cells(1, col)
            if not cell.HasFormula:
                cell.Formula = table.ListColumns(col).DataBodyRange.Cells(1, 1).Formula
            if not cell.HasFormula:
                raise RuntimeError(f"A fórmula da coluna {col} não foi preservada.")
        excel.CalculateFull()
        workbook.Save()
        return row_number
    finally:
        _close_excel(excel, workbook)



def _run_excel(operation: str, path: Path, expense: Expense | None = None) -> dict:
    import json
    import subprocess
    import sys
    command = [sys.executable, "-m", "finance_bot.excel_worker", operation, str(path)]
    if expense is not None:
        command.append(json.dumps(expense.as_dict(), ensure_ascii=False))
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError("O Excel não concluiu a operação. A planilha online não foi alterada.")
    try:
        return json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("O Excel não retornou um resultado válido.") from exc


def inspect_workbook(path: Path) -> tuple[list[str], list[str]]:
    result = _run_excel("inspect", path)
    return result["categories"], result["methods"]


def append_expense(path: Path, expense: Expense) -> int:
    return int(_run_excel("append", path, expense)["row"])

