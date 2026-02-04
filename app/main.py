from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import uuid
from sqlalchemy import select, func, text
from app.routers import health, produtos, usuarios, clientes, vendas, auth, categorias, ws, tenants, sync
from app.routers import metricas, relatorios, empresa_config, admin, dividas
from app.routers import public_menu
from app.routers import payments_mock
from app.routers import public_pedidos
from app.routers import payments
from app.routers import public_distancia
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
            tenant_uuid: uuid.UUID | None = None

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
            await conn.execute(text("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_system BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS slug VARCHAR(80)"))

            # Em alguns bancos já existentes, a coluna pode ter sido criada como NOT NULL sem default.
            # Garantimos default e preenchemos NULLs para evitar falha no bootstrap.
            await conn.execute(text("ALTER TABLE tenants ALTER COLUMN is_system SET DEFAULT FALSE"))
            await conn.execute(text("UPDATE tenants SET is_system = FALSE WHERE is_system IS NULL"))

            # Melhor esforço: garantir unicidade por índice (evita duplicação de slugs)
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_slug ON tenants (slug)"))

            # O sistema não cria mais "Negócio padrão" automaticamente.
            # Usamos os tenants de sistema como base fixa.
            tenant_uuid = uuid.UUID("22222222-2222-2222-2222-222222222222")

            # Garantir tenants padrão (protegidos) para Mercearia e Restaurante
            # Esses tenants são ocultáveis na UI e não podem ser removidos.
            await conn.execute(
                text(
                    """
                    INSERT INTO tenants (id, nome, ativo, tipo_negocio, is_system)
                    VALUES
                        ('22222222-2222-2222-2222-222222222222', 'Mercearia', TRUE, 'mercearia', TRUE),
                        ('33333333-3333-3333-3333-333333333333', 'Restaurante', TRUE, 'restaurante', TRUE)
                    ON CONFLICT (id) DO NOTHING;
                    """
                )
            )

            # Garantir slugs fixos dos tenants padrão
            await conn.execute(text("UPDATE tenants SET slug = 'mercearia' WHERE id = '22222222-2222-2222-2222-222222222222' AND (slug IS NULL OR slug = '')"))
            await conn.execute(text("UPDATE tenants SET slug = 'restaurante' WHERE id = '33333333-3333-3333-3333-333333333333' AND (slug IS NULL OR slug = '')"))

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
                    tenant_id=tenant_uuid,
                )
                session.add(user)
                await session.commit()
            else:
                # Garantir tenant_id do usuário técnico
                try:
                    if getattr(user, "is_admin", None) is not True:
                        user.is_admin = True
                    if getattr(user, "ativo", None) is not True:
                        user.ativo = True
                    if getattr(user, "tenant_id", None) is None:
                        user.tenant_id = tenant_uuid
                    await session.commit()
                except Exception:
                    pass

            # Garantir usuário admin Marrapaz
            result2 = await session.execute(
                select(User).where(func.lower(User.usuario) == func.lower("Marrapaz"))
            )
            user2 = result2.scalar_one_or_none()
            restaurante_tenant_uuid = uuid.UUID("33333333-3333-3333-3333-333333333333")
            if not user2:
                marrapaz_pass = os.getenv("MARRAPAZ_ADMIN_PASS") or "603684"
                user2 = User(
                    nome="Saide Adamo Marrapaz",
                    usuario="Marrapaz",
                    senha_hash=get_password_hash(marrapaz_pass),
                    is_admin=True,
                    ativo=True,
                    tenant_id=restaurante_tenant_uuid,
                )
                session.add(user2)
                await session.commit()
            else:
                # Garantir tenant_id do usuário (para aparecer no tenant Restaurante no PDV)
                try:
                    if getattr(user2, "tenant_id", None) is None:
                        user2.tenant_id = restaurante_tenant_uuid
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

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DEFAULT_MEDIA_DIR = os.path.join(_BACKEND_ROOT, "media")
MEDIA_DIR = os.getenv("MEDIA_DIR") or _DEFAULT_MEDIA_DIR
if not os.path.isabs(MEDIA_DIR):
    MEDIA_DIR = os.path.abspath(os.path.join(_BACKEND_ROOT, MEDIA_DIR))
os.environ["MEDIA_DIR"] = MEDIA_DIR
os.makedirs(MEDIA_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

# CORS (Cross-Origin Resource Sharing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for hybrid client access
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir routers
app.include_router(health.router)
app.include_router(categorias.router)
app.include_router(produtos.router)
app.include_router(public_menu.router)
app.include_router(public_pedidos.router)
app.include_router(public_distancia.router)
app.include_router(usuarios.router)
app.include_router(clientes.router)
app.include_router(vendas.router)
app.include_router(metricas.router)
app.include_router(auth.router)
app.include_router(sync.router)
app.include_router(ws.router)
app.include_router(relatorios.router)
app.include_router(empresa_config.router)
app.include_router(tenants.router)
app.include_router(admin.router)
app.include_router(dividas.router)
app.include_router(payments_mock.router)
app.include_router(payments.router)

@app.get("/")
async def read_root():
    return {"message": "PDV3 Backend is running!"}


@app.get("/api/restaurant-status")
async def restaurant_status():
    return {"is_open": True}
app.include_router(admin.router)
app.include_router(dividas.router)
app.include_router(payments_mock.router)
app.include_router(payments.router)

@app.get("/")
async def read_root():
    return {"message": "PDV3 Backend is running!"}


@app.get("/api/restaurant-status")
async def restaurant_status():
    return {"is_open": True}
