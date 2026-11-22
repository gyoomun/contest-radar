-- 수집할 사이트
CREATE TABLE sources (
  id        serial PRIMARY KEY,
  name      text NOT NULL UNIQUE,
  list_url  text NOT NULL,
  kind      text NOT NULL CHECK (kind IN ('rss', 'api', 'html')),
  enabled   boolean NOT NULL DEFAULT true
);

-- 공고 (url 이 같은 공고를 두 번 넣지 않는 기준)
CREATE TABLE notices (
  id             serial PRIMARY KEY,
  source_id      int NOT NULL REFERENCES sources(id),
  url            text NOT NULL UNIQUE,
  title          text NOT NULL,
  posted_at      date,
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  status         text NOT NULL DEFAULT 'new'
                 CHECK (status IN ('new', 'detailed', 'judged', 'failed')),
  organizer      text,
  apply_start    date,
  apply_end      date,
  detail         jsonb,  -- Claude 가 뽑은 참가대상·제한·지원내용·제출서류
  detail_text    text,   -- 상세 원문 (다시 뽑을 때 재수집 없이)
  error          text
);
CREATE INDEX notices_status_idx ON notices (status);
CREATE INDEX notices_apply_end_idx ON notices (apply_end);

-- 참가 가능 판정 (팀 프로필이 바뀌면 profile_hash 가 달라져 새로 판정)
CREATE TABLE judgments (
  notice_id     int NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
  profile_hash  text NOT NULL,
  verdict       text NOT NULL CHECK (verdict IN ('ok', 'maybe', 'no')),
  reason        text,
  judged_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (notice_id, profile_hash)
);

-- 수집 기록 (사이트별 0건 알림용)
CREATE TABLE runs (
  id         serial PRIMARY KEY,
  source_id  int NOT NULL REFERENCES sources(id),
  ran_at     timestamptz NOT NULL DEFAULT now(),
  found      int NOT NULL DEFAULT 0,
  new_count  int NOT NULL DEFAULT 0,
  error      text
);
