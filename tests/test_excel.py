import os
import shutil
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from finance_bot.core import Expense
from finance_bot.integrations import append_expense, inspect_workbook


@pytest.mark.skipif(not os.environ.get("FINANCE_BOT_SAMPLE_XLSX"), reason="Set sample workbook path")
def test_real_workbook_copy_keeps_formulas_and_charts(tmp_path):
    target = tmp_path / "sample.xlsx"
    shutil.copy2(Path(os.environ["FINANCE_BOT_SAMPLE_XLSX"]), target)
    categories, methods = inspect_workbook(target)
    assert "Alimentação" in categories
    assert "Crédito" in methods
    row = append_expense(
        target, Expense("2026-09-27", "54.90", "Teste do bot", "Alimentação", "Crédito", 3)
    )
    next_row = append_expense(
        target, Expense("2026-09-28", "12.00", "Segundo teste", "Alimentação", "Pix")
    )
    assert next_row > row
    with ZipFile(target) as archive:
        table = ElementTree.fromstring(archive.read("xl/tables/table1.xml"))
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet2.xml"))
        assert int(table.attrib["ref"].split(":")[1][1:]) >= next_row
        assert sheet.find(f".//{{*}}c[@r='C{row}']") is not None
        assert all(sheet.find(f".//{{*}}c[@r='{col}{r}']/{{*}}f") is not None
                   for r in (row, next_row) for col in "IJKLMN")
        assert len([name for name in archive.namelist() if name.startswith("xl/charts/chart")]) >= 2