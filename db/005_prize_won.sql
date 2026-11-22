-- 1등 상금·상품의 금액(원). 상금 순 정렬(현금 + 금액이 적힌 상품 환산액)에 쓴다
ALTER TABLE notices ADD COLUMN IF NOT EXISTS top_prize_won bigint;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS top_gift_won  bigint;
