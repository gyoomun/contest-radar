-- 출처 묶기(notice_links) + 상세 칸. 이미 띄운 DB 에 한 번 실행한다(새로 띄우면 schema.sql 에 들어 있음)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 공고명 비교용: 소괄호 안 글(재공고·~10.13 등)과 기호·띄어쓰기를 지운다. 씽굿의 '중복 //' 머리도 지운다. [세종] 같은 지역 표시는 남긴다
CREATE OR REPLACE FUNCTION norm_title(t text) RETURNS text IMMUTABLE LANGUAGE sql AS $$
  SELECT lower(regexp_replace(regexp_replace(t, '\([^)]*\)|^\s*중복\s*//', '', 'g'), '[^가-힣a-zA-Z0-9]', '', 'g'))
$$;
CREATE INDEX IF NOT EXISTS notices_title_trgm ON notices USING gin (norm_title(title) gin_trgm_ops);

-- 사이트별 링크. 같은 공고가 여러 사이트에 있으면 notice_id 하나에 링크가 여러 개
CREATE TABLE IF NOT EXISTS notice_links (
  url            text PRIMARY KEY,
  notice_id      int NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
  source_id      int NOT NULL REFERENCES sources(id),
  title          text NOT NULL,
  site_category  text,
  first_seen_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notice_links_notice_idx ON notice_links (notice_id);
INSERT INTO notice_links (url, notice_id, source_id, title, first_seen_at)
SELECT url, id, source_id, title, first_seen_at FROM notices ON CONFLICT DO NOTHING;

-- 상세 읽기로 채우는 칸
ALTER TABLE notices
  ADD COLUMN IF NOT EXISTS category      text[],
  ADD COLUMN IF NOT EXISTS site_category text,
  ADD COLUMN IF NOT EXISTS is_recruit    boolean,
  ADD COLUMN IF NOT EXISTS summary       text,
  ADD COLUMN IF NOT EXISTS target        text,
  ADD COLUMN IF NOT EXISTS target_types  text[],
  ADD COLUMN IF NOT EXISTS age_min       int,
  ADD COLUMN IF NOT EXISTS age_max       int,
  ADD COLUMN IF NOT EXISTS regions       text[],
  ADD COLUMN IF NOT EXISTS biz_age_max   numeric,
  ADD COLUMN IF NOT EXISTS benefit       text,
  ADD COLUMN IF NOT EXISTS amount_max    bigint,
  ADD COLUMN IF NOT EXISTS documents     text;
CREATE INDEX IF NOT EXISTS notices_category_idx ON notices USING gin (category);
CREATE INDEX IF NOT EXISTS notices_regions_idx ON notices USING gin (regions);
CREATE INDEX IF NOT EXISTS notices_target_types_idx ON notices USING gin (target_types);
