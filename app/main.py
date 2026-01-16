from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import uuid
from sqlalchemy import select, func, text
from app.routers import health, produtos, usuarios, clientes, vendas, auth, categorias, ws, tenants
from app.routers import metricas, relatorios, empresa_config, admin, dividas
from app.db.session import engine, AsyncSessionLocal
from app.db.base import DeclarativeBase
from app.db.models import User
from app.core.security import get_password_hash

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Verificar e criar tabelas se necessário
    print("Iniciando backend...")
    try:
        async with engine.begin() as conn:
            print("Verificando estrutura do PostgreSQL...")
            # Garantir schema do PDV (evita conflito com tabelas do restaurante em public)
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS pdv"))
            await conn.run_sync(DeclarativeBase.metadata.create_all)
            print("Estrutura do banco verificada!")

            # --- Multi-tenant bootstrap (Opção A) ---
            # Observação: create_all() não aplica alterações em tabelas existentes.
            # Então garantimos via SQL que a tabela tenants e colunas tenant_id existam.
            default_tenant_id = os.getenv("DEFAULT_TENANT_ID")
            default_tenant_name = os.getenv("DEFAULT_TENANT_NAME", "Negócio padrão")
            tenant_uuid: uuid.UUID | None = None
            if default_tenant_id:
                try:
                    tenant_uuid = uuid.UUID(default_tenant_id)
                except Exception:
                    tenant_uuid = None

            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS tenants (
                        id UUID PRIMARY KEY,
                        created_at TIMESTAMPTZ DEFAULT now(),
                        updated_at TIMESTAMPTZ DEFAULT now(),
                        nome VARCHAR(200) NOT NULL,
                        ativo BOOLEAN DEFAULT TRUE,
                        tipo_negocio VARCHAR(50) DEFAULT 'mercearia'
                    );
                    """
                )
            )

            await conn.execute(text("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS tipo_negocio VARCHAR(50) DEFAULT 'mercearia'"))

            should_insert_default = False
            if tenant_uuid is None:
                existing_id = (await conn.execute(
                    text(
                        """
                        SELECT id
                        FROM tenants
                        ORDER BY created_at
                        LIMIT 1
                        """
                    )
                )).scalar_one_or_none()
                if existing_id:
                    tenant_uuid = existing_id
                else:
                    tenant_uuid = uuid.uuid4()
                    should_insert_default = True
            else:
                should_insert_default = True

            if should_insert_default:
                await conn.execute(
                    text(
                        """
                        INSERT INTO tenants (id, nome, ativo, tipo_negocio)
                        VALUES (:id, :nome, TRUE, :tipo)
                        ON CONFLICT (id) DO NOTHING;
                        """
                    ),
                    {"id": tenant_uuid, "nome": default_tenant_name, "tipo": os.getenv("DEFAULT_TENANT_TIPO", "mercearia")},
                )

            # Garantir colunas tenant_id nas tabelas principais (nullable por enquanto)
            for table in [
                "pdv.usuarios",
                "pdv.produtos",
                "pdv.clientes",
                "pdv.vendas",
                "pdv.empresa_config",
                "pdv.dividas",
                "pdv.itens_venda",
                "pdv.itens_divida",
                "pdv.pagamentos_divida",
            ]:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id UUID"))
                idx_name = table.replace('.', '_')
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{idx_name}_tenant_id ON {table} (tenant_id)"))

            await conn.execute(text("ALTER TABLE pdv.produtos ADD COLUMN IF NOT EXISTS imagem_path VARCHAR(255)"))

            # Preencher tenant_id default em registros existentes (mantém compatibilidade)
            for table in [
                "pdv.usuarios",
                "pdv.produtos",
                "pdv.clientes",
                "pdv.vendas",
                "pdv.empresa_config",
                "pdv.dividas",
                "pdv.itens_venda",
                "pdv.itens_divida",
                "pdv.pagamentos_divida",
            ]:
                await conn.execute(text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"), {"tid": tenant_uuid})

        # Garantir usuário técnico Neotrix para autoLogin do PDV online
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(func.lower(User.usuario) == func.lower("Neotrix"))
            )
            user = result.scalar_one_or_none()
            if not user:
                neotrix_pass = os.getenv("NEOTRIX_ADMIN_PASS") or "842384"
                user = User(
                    nome="Neotrix Tecnologias",
                    usuario="Neotrix",
                    senha_hash=get_password_hash(neotrix_pass),
                    is_admin=True,
                    ativo=True,
                )
                session.add(user)
                await session.commit()
            else:
                # Garantir tenant_id do usuário técnico
                try:
                    if getattr(user, "tenant_id", None) is None:
                        user.tenant_id = tenant_uuid
                        await session.commit()
                except Exception:
                    pass
    except Exception as e:
        print(f"Erro ao conectar com o banco: {e}")
        # Continue mesmo com erro de banco para permitir healthcheck
        pass
    
    yield
    
    # Shutdown
    print("Encerrando backend...")
    try:
        await engine.dispose()
    except:
        pass

app = FastAPI(
    title="PDV3 Hybrid Backend",
    description="API for PDV3 online/offline synchronization.",
    version="0.1.0",
    lifespan=lifespan
)

MEDIA_DIR = os.getenv("MEDIA_DIR", "media")
os.makedirs(MEDIA_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

# CORS (Cross-Origin Resource Sharing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for hybrid client access
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir routers
app.include_router(health.router)
app.include_router(categorias.router)
app.include_router(produtos.router)
app.include_router(usuarios.router)
app.include_router(clientes.router)
app.include_router(vendas.router)
app.include_router(metricas.router)
app.include_router(auth.router)
app.include_router(ws.router)
app.include_router(relatorios.router)
app.include_router(empresa_config.router)
app.include_router(tenants.router)
app.include_router(admin.router)
app.include_router(dividas.router)

@app.get("/")
async def read_root():
    return {"message": "PDV3 Backend is running!"}
