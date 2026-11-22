-- 총상금·지원금. 1등 상금(top_prize)과 따로 한눈에 보고 정렬하려고 둔다
ALTER TABLE notices ADD COLUMN IF NOT EXISTS total_prize     text;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS total_prize_won bigint;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS support         text;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS support_won     bigint;
