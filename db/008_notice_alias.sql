-- 묶기로 사라진 공고 번호 → 합쳐진 공고 번호. 예전 번호로 검색해도 찾게
CREATE TABLE IF NOT EXISTS notice_alias (
    old_id    integer PRIMARY KEY,
    notice_id integer NOT NULL REFERENCES notices(id) ON DELETE CASCADE
);
