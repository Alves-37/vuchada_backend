-- Ensure schema exists
CREATE SCHEMA IF NOT EXISTS pdv;

-- Payment transactions table (used by /api/payments and /public/distancia/checkout)
CREATE TABLE IF NOT EXISTS pdv.payment_transactions (
  id UUID PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),

  tenant_id UUID NULL,
  venda_id UUID NULL,

  provider VARCHAR(20) NOT NULL,
  phone VARCHAR(30) NULL,
  amount DOUBLE PRECISION DEFAULT 0.0,
  currency VARCHAR(10) DEFAULT 'MZN',
  status VARCHAR(20) DEFAULT 'pending',
  provider_reference VARCHAR(100) NULL
);

-- Foreign keys (add if not exists pattern via exception-safe DO blocks)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE c.conname = 'fk_payment_transactions_tenant_id'
      AND n.nspname = 'pdv'
      AND t.relname = 'payment_transactions'
  ) THEN
    ALTER TABLE pdv.payment_transactions
      ADD CONSTRAINT fk_payment_transactions_tenant_id
      FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE c.conname = 'fk_payment_transactions_venda_id'
      AND n.nspname = 'pdv'
      AND t.relname = 'payment_transactions'
  ) THEN
    ALTER TABLE pdv.payment_transactions
      ADD CONSTRAINT fk_payment_transactions_venda_id
      FOREIGN KEY (venda_id) REFERENCES pdv.vendas(id);
  END IF;
END $$;

-- Indexes
CREATE INDEX IF NOT EXISTS ix_pdv_payment_transactions_tenant_id ON pdv.payment_transactions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_pdv_payment_transactions_venda_id ON pdv.payment_transactions(venda_id);
CREATE INDEX IF NOT EXISTS ix_pdv_payment_transactions_status ON pdv.payment_transactions(status);
