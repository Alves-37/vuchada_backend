# Backend FastAPI (Postgres) - NEOPDV

## Setup

1) Criar e ativar venv

2) Instalar dependências:

```bash
pip install -r requirements.txt
```

3) Configurar Postgres

- Crie um banco `neopdv` (ou altere a URL).
- Crie um arquivo `.env` (baseado em `.env.example`) com:

```env
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/neopdv
```

## Rodar API

Na pasta `backend/fastapi`:

```bash
uvicorn app.main:app --reload --port 8000
```

Abra:
- `http://localhost:8000/health`
- `http://localhost:8000/docs`

## Endpoints principais

- `GET /produtos`
- `POST /produtos`
- `GET /mesas`
- `POST /mesas`
- `GET /pedidos`
- `POST /pedidos` (aceita itens)
- `POST /pedidos/{id}/fechar`
