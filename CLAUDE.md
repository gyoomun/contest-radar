# contest-radar

공모전·지원사업 공고를 10개 사이트에서 모아 Postgres에 넣고, 상세·첨부를 `claude -p`(haiku)로 읽어 참가자격을 컬럼으로 만든 뒤 뷰어로 본다. 팀 프로필로 참가 가능 여부를 가리는 단계(judgments 테이블)와 매일 예약 실행은 아직 없다.

## 실행·반영
- DB: `docker compose up -d` (컨테이너 research-postgres, 포트 15434, db/user/pw `research`)
- `uv run collect.py [사이트이름…]` 목록 수집 → 끝에 중복 묶기. `--merge-all` 은 전체를 다시 묶는다
- `uv run detail.py [--id N]` status new·failed 인 공고의 상세를 읽는다. 실패는 failed 로 남고 다음 실행에 다시 읽는다
- `uv run viewer.py` → http://127.0.0.1:8765. PAGE(HTML)를 고치면 뷰어를 다시 켜야 반영된다. 데이터는 새로고침만

## 구조
- collect.py: 사이트별 목록 수집(SITES 딕셔너리, sources 테이블의 list_url 은 참고용), 중복 묶기(merge_duplicates)
- sites_detail.py: 사이트별 상세 본문·첨부 목록 `get(url)`
- detail.py: 첨부 내려받기·글 뽑기(hwp5txt, pdftotext, hwpx unzip), Claude 로 컬럼 추출
- viewer.py: 표준 라이브러리 HTTP 서버 + 한 장짜리 HTML
- db/*.sql: 스키마. 새 DB 를 띄울 때만 자동 적용되므로 고치면 실행 중인 DB 에도 직접 적용한다

## 핵심 개념
- notices = 공고 하나, notice_links = 그 공고가 올라온 사이트별 주소. 중복이면 행을 지우고 링크만 남은 공고로 옮긴다
- 분야(category)·대상(target_types)·지역(regions)은 detail.py 의 고정 목록에서만 고른다. 사이트 원래 분류는 site_category
- 묶기: 제목 trigram 0.4 이상이 후보. 괄호·기호 뺀 제목이 같으면 바로 합치고, 나머지는 Claude 가 주최·접수기간·대상·요약까지 보고 판단(collect.py SAME_RULES). 같은 게시판 글끼리는 후보로 안 본다(씽굿 "중복 //" 글만 예외). 시·도 태그나 제목 연도가 다르면 묶지 않는다
- `--check-groups`: 이미 묶인 공고를 같은 규칙으로 다시 보고 다른 공고인 링크를 떼어 새 공고(status new)로 만든다. 뗀 뒤 detail.py 로 상세를 읽힌다

## 함정
- docker-compose 의 볼륨 이름 `06research_research-pgdata` 는 폴더를 옮기기 전 이름이다. 바꾸면 빈 DB 가 뜬다
- 인천테크노파크는 인증서 검증을 끈다(NO_VERIFY). 한때 파이썬에서 인증서 오류가 났다
- 창조경제혁신센터 상세 주소에는 `&kind=all` 이 있어야 다른 지역 글이 열린다
- 기업마당 RSS 는 crtfcKey 없이는 항목이 비어 HTML 목록을 쓴다. 첨부 링크는 downloadToken 이 붙은 href 그대로 써야 한다
- 대티즌 키는 사이트 JS 에서 그때그때 읽는다(공개 publishable 키). 코드에 박지 않는다

## 남은 일
- 묶음끼리 합칠 때 겹침 검사(clash)는 이번 실행에서 본 공고만 본다. 그래서 이미 묶인 공고에 같은 사이트의 재등록 글(같은 제목, 다른 번호)이 들어올 수 있다
- 마감일 모름 1,119건 중 놓친 날짜가 얼마나 되는지 확인
