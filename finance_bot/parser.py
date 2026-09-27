from __future__ import annotations

import json
from datetime import date

from openai import OpenAI
from pydantic import BaseModel

from .core import Expense


class ModelPatch(BaseModel):
    date: str | None
    amount: str | None
    description: str | None
    category: str | None
    payment_method: str | None
    installments: int | None
    invoice_month: str | None
    multiple_expenses: bool


class ExpenseParser:
    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key, timeout=30.0, max_retries=1)

    def parse(self, text: str, previous: Expense, categories: list[str],
              methods: list[str], current_date: date) -> dict:
        instructions = (
            "Extraia campos de UMA despesa de uma mensagem em português do Brasil. "
            "Retorne só dados expressos na mensagem atual; use o rascunho apenas para entender correções. "
            "Campo não informado na mensagem atual deve ser null. Não adivinhe data, categoria ou meio. "
            "Converta data explícita ou relativa (hoje, ontem) para AAAA-MM-DD usando a data atual fornecida. "
            "Converta valor para string decimal com ponto e duas casas; não altere o valor. "
            "Descrição deve ser curta e fiel ao texto. Categoria e meio devem ser uma opção das listas, "
            "somente quando a mensagem permitir identificá-los claramente; caso contrário, null. "
            "Para '3x' extraia installments=3. Fatura manual, se explícita, vira o primeiro dia do mês "
            "em AAAA-MM-01. Se houver mais de uma despesa, marque multiple_expenses=true."
        )
        payload = {
            "mensagem": text,
            "rascunho_anterior": previous.as_dict(),
            "data_atual": current_date.isoformat(),
            "categorias_gasto": categories,
            "meios_pagamento": methods,
        }
        response = self.client.responses.parse(
            model="gpt-6-luna",
            reasoning={"effort": "low"},
            input=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            text_format=ModelPatch,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("O modelo não conseguiu interpretar a mensagem.")
        return parsed.model_dump()
