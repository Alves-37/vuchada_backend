-- Ensure schema exists
CREATE SCHEMA IF NOT EXISTS pdv;

-- Add menu-digital order flow fields to pdv.vendas
ALTER TABLE pdv.vendas ADD COLUMN IF NOT EXISTS tipo_pedido VARCHAR(20);
ALTER TABLE pdv.vendas ADD COLUMN IF NOT EXISTS status_pedido VARCHAR(30);
ALTER TABLE pdv.vendas ADD COLUMN IF NOT EXISTS mesa_id INTEGER;
ALTER TABLE pdv.vendas ADD COLUMN IF NOT EXISTS lugar_numero INTEGER;
ALTER TABLE pdv.vendas ADD COLUMN IF NOT EXISTS distancia_tipo VARCHAR(20);
ALTER TABLE pdv.vendas ADD COLUMN IF NOT EXISTS cliente_nome VARCHAR(100);
ALTER TABLE pdv.vendas ADD COLUMN IF NOT EXISTS cliente_telefone VARCHAR(30);
ALTER TABLE pdv.vendas ADD COLUMN IF NOT EXISTS endereco_entrega TEXT;
ALTER TABLE pdv.vendas ADD COLUMN IF NOT EXISTS taxa_entrega FLOAT DEFAULT 0;

UPDATE pdv.vendas SET taxa_entrega = 0 WHERE taxa_entrega IS NULL;
