-- 시상: 1등 상금·1등 상품(상품권 등)·최고 상격. 뷰어에서 한눈에 보려고 따로 둔다
ALTER TABLE notices ADD COLUMN IF NOT EXISTS top_prize text;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS top_gift  text;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS award     text;
