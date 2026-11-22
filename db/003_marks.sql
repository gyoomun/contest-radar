-- 뷰어에서 남기는 관심 표시·메모
CREATE TABLE IF NOT EXISTS marks (
    notice_id  integer PRIMARY KEY REFERENCES notices(id) ON DELETE CASCADE,
    star       boolean NOT NULL DEFAULT false,
    memo       text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
