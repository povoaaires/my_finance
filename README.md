# Bot de Finanças

Basicamente é um bot que receberá, por mensagem no Telegram, uma mensagem indicando uma despesa, e a partir dai será inserido na tabela tblLanc de uma planilha Excel no OneDrive. Esse bot inicialmente será executado em uma máquina pessoal, mas posteriormente pretendo subi-la em uma VPS, com Excel desktop instalado.

## Preparação

1. Instale [uv](https://docs.astral.sh/uv/) e execute `uv sync --extra test` na pasta do projeto. É necessário Python 3.12 ou superior.
2. Crie um bot com o BotFather no Telegram e guarde o token.
3. Obtenha uma chave da API da OpenAI. A assinatura do ChatGPT não substitui a chave da API.
4. Confirme que o aplicativo OneDrive está conectado à sua conta pessoal e que `C:\Users\seu_user\OneDrive\Finanças\2026.xlsx` aparece no Explorador de Arquivos. Marque o arquivo como **Sempre manter neste dispositivo** e confira que ele abre no Excel desktop.
5. Preencha o arquivo `.env` na raiz do projeto (ele é ignorado pelo Git):

```dotenv
TELEGRAM_BOT_TOKEN=token_do_BotFather
TELEGRAM_USER_ID=seu_id_numerico
OPENAI_API_KEY=sua_chave_da_API
FINANCE_BOT_WORKBOOK_DIR=C:\Users\seu_user\OneDrive\Finanças
```

A pasta indicada já é o padrão neste computador; a última linha pode ser omitida. Para descobrir seu ID, envie uma mensagem privada ao bot e execute `uv run finance-bot id` enquanto o bot estiver parado. O bot lê o `.env` automaticamente. Variáveis do ambiente têm prioridade sobre o `.env`.

Inicie o bot com `uv run finance-bot`. O terminal precisa permanecer aberto e o computador ligado.

## Uso

No chat privado do bot:

```text
/vincular_ano 2026
27/09/2026 mercado R$ 54,90, Alimentação, Pix
```

`/vincular_ano 2026` procura **exatamente** `2026.xlsx` na pasta configurada, verifica `tblLanc` e as opções da aba `Config`, e registra o ano. Para 2027, prepare `2027.xlsx` na mesma pasta e use `/vincular_ano 2027`. Um ano sem arquivo vinculado não recebe despesas.

O bot exige data, valor, descrição, categoria do grupo **Gasto** e meio de pagamento cadastrado na aba **Config**. Ele pergunta pelos campos ausentes e só grava depois de tocar em **Salvar**. Envie outra mensagem para corrigir um rascunho, use `/cancelar` para descartá-lo e `/status` para listar os anos vinculados.

Depois da confirmação, o Excel salva o arquivo local e o OneDrive o sincroniza. O bot confere se o arquivo local mudou durante a validação, mas não recebe confirmação de que a versão online já foi atualizada. Aguarde o ícone do OneDrive indicar sincronização concluída antes de editar a planilha em outro dispositivo. Se o bot disser que o resultado da gravação é incerto, confira o arquivo local antes de tentar novamente.

Rascunhos e vínculos anuais ficam em `~/.my_finance_bot/state.sqlite3`. Vínculos antigos feitos com links do Microsoft Graph não são usados neste modo; vincule o ano novamente pelo comando local.

## Testes

```powershell
uv run pytest -q
$env:FINANCE_BOT_SAMPLE_XLSX = "C:\Users\seu_user\OneDrive\Finanças\2026.xlsx"
uv run pytest -q tests\test_excel.py
```

O teste com Excel copia a planilha para uma pasta temporária e não modifica o original. A integração real com Telegram e OpenAI depende das credenciais configuradas no computador.
