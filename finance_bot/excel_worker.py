from __future__ import annotations

import json
import sys
from pathlib import Path

from .core import Expense
from .integrations import _append_expense_local, _inspect_workbook_local


def main() -> None:
    operation = sys.argv[1]
    path = Path(sys.argv[2])
    if operation == "inspect":
        categories, methods = _inspect_workbook_local(path)
        result = {"categories": categories, "methods": methods}
    elif operation == "append":
        expense = Expense.from_dict(json.loads(sys.argv[3]))
        result = {"row": _append_expense_local(path, expense)}
    else:
        raise ValueError("Operação inválida")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()