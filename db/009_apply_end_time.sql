-- 접수 마감 시각(예: 18:00). 없으면 그날 끝까지로 본다
ALTER TABLE notices ADD COLUMN IF NOT EXISTS apply_end_time time;
