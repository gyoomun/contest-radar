-- 공고명 비교용 정규화: 괄호 안 글자는 지우지 않는다(영어 공식 이름 등이 괄호에 있어 지우면 같은 공고를 못 찾았다). '중복 //' 머리만 뗀다
CREATE OR REPLACE FUNCTION norm_title(t text) RETURNS text IMMUTABLE LANGUAGE sql AS $$
  SELECT lower(regexp_replace(regexp_replace(t, '^\s*중복\s*//', '', 'g'), '[^가-힣a-zA-Z0-9]', '', 'g'))
$$;
REINDEX INDEX notices_title_trgm;
