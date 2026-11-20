# estoubem
Plataforma em desenvolvimento para acompanhamento do bem-estar de pessoas idosas.

## Demonstração local

O MVP inclui uma interface em português e uma API FastAPI com banco SQLite. Os canais de telefone e WhatsApp são apenas simulados: nenhuma ligação ou mensagem é enviada, e pedidos de ajuda ficam registrados localmente sem avisar cuidadores.

Requer Python 3.11 ou superior. Para instalar e iniciar:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
uvicorn estoubem.main:app --app-dir src --host 127.0.0.1 --port 8000
```

Abra <http://127.0.0.1:8000>. A documentação interativa da API fica em <http://127.0.0.1:8000/docs>. O banco é criado em `data/estoubem.sqlite3`; para testes, execute `pytest`.

Esta demonstração não tem autenticação, integração com provedores, agendamento em segundo plano ou garantias de disponibilidade. Não use para monitorar pessoas reais ou em situações de emergência. Não exponha o servidor à internet.

A arquitetura completa pretendida, incluindo integrações reais e requisitos de produção, continua descrita como proposta em [docs/proposta-arquitetura-multicanal.md](docs/proposta-arquitetura-multicanal.md).
