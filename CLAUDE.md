# contest-radar

공모전·지원사업 공고를 10개 사이트에서 모아 Postgres에 넣고, 상세·첨부를 `claude -p`(haiku)로 읽어 참가자격을 컬럼으로 만든 뒤 뷰어로 본다. 팀 프로필로 참가 가능 여부를 가리는 단계(judgments 테이블)는 아직 없다.

## 실행·반영
- DB: `docker compose up -d` (컨테이너 research-postgres, 포트 15434, db/user/pw `research`)
- `uv run collect.py [사이트이름…]` 목록 수집 → 끝에 중복 묶기. `--merge-all` 은 전체를 다시 묶는다
- `uv run detail.py [--id N]` status new·failed 인 공고의 상세를 읽는다. 실패는 failed 로 남고 다음 실행에 다시 읽는다
- launchd 가 1시간마다·로그인 때 `daily.sh` 를 부르고, 08:30 이 지났고 오늘 아직 안 돌았을 때만 돈다(일지 자동 정리와 같은 시각. 맥이 꺼져 있던 날도 켜진 뒤 돈다): compose up → collect.py → detail.py 2번(일시 실패 재시도). 끝까지 돈 날짜는 `~/Library/Logs/contest-radar/last-done`
  - 뷰어의 [지금 수집] 버튼은 `daily.sh --force` 로 시각·날짜 검사 없이 바로 돌린다
  - 설치: `ln -sf $PWD/launchd/com.gyoomun.contest-radar.plist ~/Library/LaunchAgents/` 후 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gyoomun.contest-radar.plist`
  - 지금 돌리기: `launchctl kickstart -k gui/$(id -u)/com.gyoomun.contest-radar` / 끄기: `launchctl bootout gui/$(id -u)/com.gyoomun.contest-radar`
  - 로그: `~/Library/Logs/contest-radar/YYYY-MM-DD.log`. 겹쳐 돌지 않게 `/tmp/contest-radar.lock` 폴더로 잠근다(죽은 실행이 남기면 지운다)
  - 사이트가 실패하거나 단계가 실패하면 맥 알림(osascript)을 띄운다(대리님이 못 볼 때가 있다). 뷰어 맨 위에도 마지막 실행 실패 사이트와 7일째 새 글 없는 사이트를 띄운다
  - plist 의 PATH 에 claude·uv·docker·pdftotext 폴더를 박아 두었다. 어느 하나 위치가 바뀌면 plist 를 고치고 bootout·bootstrap 한다
- 뷰어: launchd `com.gyoomun.contest-radar-viewer`(launchd/ 의 plist, KeepAlive)가 늘 띄워 둔다 → http://127.0.0.1:8765, 밖에서는 https://radar.gyoomun.com
  - 고친 화면은 먼저 `PORT=8766 uv run viewer.py` 로 로컬에서 보여 주고, 대리님이 "배포"라고 하면 커밋 후 `launchctl kickstart -k gui/$(id -u)/com.gyoomun.contest-radar-viewer` 로 반영한다(대리님 결정: 배포 전에 로컬 먼저). 데이터는 새로고침만. 로그는 `~/Library/Logs/contest-radar/viewer.log`
  - radar.gyoomun.com 은 HQ 와 같은 클라우드플레어 터널(`~/.cloudflared/config.yml` ingress, 개인 계정 gyoomun.com 의 CNAME)이다. ingress 를 고치면 `launchctl kickstart -k gui/$(id -u)/com.cloudflare.cloudflared` (HQ 접속도 몇 초 끊긴다)
  - 터널로 들어온 요청(Cf-Connecting-Ip 헤더)만 비밀번호를 묻는다. 비밀번호는 `.env` 의 RADAR_PASSWORD(저장소가 public 이라 git 에서 뺐다). 맞으면 쿠키 radar 를 1년 심는다. 바꾸면 모두 다시 로그인한다
  - 공고를 누르면 원문 페이지를 연다. 우리 화면의 요약·대상·지원은 참고용이고 확인은 원문으로 한다(대리님 결정)
  - 관심(★)·메모는 marks 테이블. 묶기로 공고가 합쳐지면 남는 공고로 옮긴다. NEW 는 first_seen_at 24시간 이내
  - PC 가 중심이다. 폰은 나중에 필요한 기능만 추려 낸다. 글자는 본문 16px·보조 14px 아래로 내리지 않는다(작으면 대리님이 바로 짜증)

## 구조
- collect.py: 사이트별 목록 수집(SITES 딕셔너리, sources 테이블의 list_url 은 참고용), 중복 묶기(merge_duplicates)
- sites_detail.py: 사이트별 상세 본문·첨부 목록 `get(url)`
- detail.py: 첨부 내려받기·글 뽑기(hwp5txt, pdftotext, hwpx unzip), Claude 로 컬럼 추출
- viewer.py: 표준 라이브러리 HTTP 서버 + 한 장짜리 HTML
- db/*.sql: 스키마. 새 DB 를 띄울 때만 자동 적용되므로 고치면 실행 중인 DB 에도 직접 적용한다

## 핵심 개념
- notices = 공고 하나, notice_links = 그 공고가 올라온 사이트별 주소. 중복이면 행을 지우고 링크만 남은 공고로 옮긴다
- 분야(category)·대상(target_types)·지역(regions)은 detail.py 의 고정 목록에서만 고른다. 사이트 원래 분류는 site_category
  - 공간 입주기업 모집은 '입주·공간', 돈·멘토링을 주는 사업은 '창업·사업화'로 나눈다(대리님 요청)
- 1등 시상: top_prize(1등 현금)·top_gift(1등 상품)·award(최고 상격). 공모전이 아닌 지원사업은 비운다. top_prize_won·top_gift_won 은 그 금액(원)이고 뷰어의 상금 순 정렬은 둘의 합(금액 없는 상품은 0, 외화는 대략 환율)
- 묶기: 제목 trigram 0.4 이상이 후보. 괄호·기호 뺀 제목이 같으면 바로 합치고, 나머지는 Claude 가 주최·접수기간·대상·요약까지 보고 판단(collect.py SAME_RULES). 같은 게시판 글끼리는 후보로 안 본다(씽굿 "중복 //" 글만 예외). 시·도 태그나 제목 연도가 다르면 묶지 않는다
- `--check-groups`: 이미 묶인 공고를 같은 규칙으로 다시 보고 다른 공고인 링크를 떼어 새 공고(status new)로 만든다. 뗀 뒤 detail.py 로 상세를 읽힌다

## 함정
- docker-compose 의 볼륨 이름 `06research_research-pgdata` 는 폴더를 옮기기 전 이름이다. 바꾸면 빈 DB 가 뜬다
- 인천테크노파크는 인증서 검증을 끈다(NO_VERIFY). 한때 파이썬에서 인증서 오류가 났다
- 창조경제혁신센터 상세 주소에는 `&kind=all` 이 있어야 다른 지역 글이 열린다
- 기업마당 RSS 는 crtfcKey 없이는 항목이 비어 HTML 목록을 쓴다. 첨부 링크는 downloadToken 이 붙은 href 그대로 써야 한다
- runs.found 는 html_site 를 쓰는 사이트(기업마당·K-Startup 등 7곳)에선 '처음 보는 글 수'다. 1쪽에 새 글이 없으면 0 이라 매일 알림의 0건이 대부분 정상이다
- 대티즌 키는 사이트 JS 에서 그때그때 읽는다(공개 publishable 키). 코드에 박지 않는다

## 남은 일
- 묶음끼리 합칠 때 겹침 검사(clash)는 이번 실행에서 본 공고만 본다. 그래서 이미 묶인 공고에 같은 사이트의 재등록 글(같은 제목, 다른 번호)이 들어올 수 있다
- 마감일 모름 1,119건 중 놓친 날짜가 얼마나 되는지 확인
