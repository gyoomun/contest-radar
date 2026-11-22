"""공고 목록 수집기: 사이트별 목록을 받아 notices 표에 넣는다.

uv run collect.py            # 켠 사이트 전부
uv run collect.py 위비티 씽굿  # 일부만
"""
import html
from concurrent.futures import ThreadPoolExecutor
import json
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

import psycopg

DSN = "postgresql://research:research@localhost:15434/research"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131 Safari/537.36"
TODAY = date.today()


# ---------- 받기 ----------

# 중간 인증서를 안 보내는 사이트. curl·브라우저는 되지만 파이썬은 검증에 실패한다(공개 공고 읽기만 하므로 검증을 끈다)
NO_VERIFY = ("www.itp.or.kr",)


def fetch(url, data=None, headers=None):
    ctx = ssl.create_default_context()
    if urllib.parse.urlsplit(url).hostname in NO_VERIFY:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    if data is not None:
        req.data = urllib.parse.urlencode(data).encode()
    for attempt in range(3):   # 시간 초과·접속 끊김은 사이트가 잠깐 느린 경우가 많아 5초·10초 쉬고 두 번 더 해 본다
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                raw = r.read()
                charset = r.headers.get_content_charset()
            break
        except urllib.error.HTTPError:
            raise   # 404·500 같은 서버 응답은 다시 해도 같다
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    if not charset:
        m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000])
        charset = m.group(1).decode() if m else "utf-8"
    return raw.decode(charset, errors="replace")


def fetch_json(url, data=None, headers=None):
    return json.loads(fetch(url, data, headers))


# ---------- 화면 → 글자+링크 (Claude 에 넘길 것) ----------

class _Text(HTMLParser):
    """script/style 을 빼고 글자만 남긴다. 링크는 [글](href | onclick | data-*) 로 남긴다."""
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self, base):
        super().__init__()
        self.base, self.out, self.skip, self.link = base, [], 0, None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        a = dict(attrs)
        hints = [a.get("onclick")] + [f"{k}={v}" for k, v in a.items() if k.startswith("data-") and v]
        href = a.get("href")
        if href and not href.startswith(("javascript", "#", "mailto")):
            href = urllib.parse.urljoin(self.base, href)
        hints = [h for h in [href, *hints] if h]
        if hints and tag in ("a", "li", "tr", "div", "button") and self.link is None:
            self.link = (tag, hints, len(self.out))
        if tag in ("br", "p", "li", "tr", "div", "h1", "h2", "h3", "h4", "dt", "dd"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip -= 1
        if self.link and self.link[0] == tag:
            _, hints, start = self.link
            text = " ".join("".join(self.out[start:]).split())
            del self.out[start:]
            self.out.append(f"[{text}]({' | '.join(hints)})" if text else "")
            self.link = None

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)


def page_text(html_src, base):
    p = _Text(base)
    p.feed(html_src)
    text = "".join(p.out)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


# ---------- Claude 로 목록 해석 ----------

LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "posted_at": {"type": ["string", "null"]},
                    "apply_end": {"type": ["string", "null"]},
                    "organizer": {"type": ["string", "null"]},
                    "site_category": {"type": ["string", "null"]},
                    "closed": {"type": "boolean"},
                },
                "required": ["title", "url", "posted_at", "apply_end", "organizer", "site_category", "closed"],
            },
        }
    },
    "required": ["items"],
}


CLAUDE_SLOTS = threading.BoundedSemaphore(6)  # 이 맥에서 동시에 띄우는 claude 수


def claude_list(text, hint):
    prompt = f"""아래는 공고·공모전 목록 페이지의 글자와 링크다([글](링크 | onclick | data-*) 꼴).
목록 본문에 있는 공고만 빠짐없이 뽑아라. 메뉴·배너·광고·인기글·이벤트 같은 목록 밖 항목은 뺀다.
- title: 공고명 그대로
- url: 상세 페이지의 절대 주소. {hint}
- posted_at: 등록일 YYYY-MM-DD, 없으면 null
- apply_end: 접수 마감일 YYYY-MM-DD, 없으면 null. D-day 만 있으면 오늘({TODAY})에서 계산
- organizer: 주최·주관·소관 기관, 없으면 null
- site_category: 목록에 보이는 분야·분류 글 그대로, 없으면 null
- closed: '마감' 표시가 있거나 마감일이 오늘 전이면 true"""
    with CLAUDE_SLOTS:
        r = _run_claude(prompt, text)
    out = json.loads(r.stdout)
    if out.get("is_error") or "structured_output" not in out:
        raise RuntimeError(f"claude 실패: {out.get('result', r.stderr)[:300]}")
    return out["structured_output"]["items"]


def _run_claude(prompt, text):
    return subprocess.run(
        ["claude", "-p", prompt, "--model", "haiku", "--output-format", "json",
         "--no-session-persistence", "--tools", "", "--json-schema", json.dumps(LIST_SCHEMA)],
        input=text, capture_output=True, text=True, timeout=300,
    )


# ---------- DB ----------

def to_date(s):
    if not s:
        return None
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s)
    return date(*map(int, m.groups())) if m else None


def is_open(item):
    end = to_date(item.get("apply_end"))
    return not item.get("closed") and (end is None or end >= TODAY)


def save(conn, source_id, items):
    """새로 넣은 건수를 돌려준다. 이미 아는 링크(url)는 건너뛴다.
    다른 사이트에 같은 공고가 있는지는 merge_duplicates() 가 뒤에서 묶는다."""
    new = 0
    for it in items:
        if conn.execute("SELECT 1 FROM notice_links WHERE url = %s", (it["url"],)).fetchone():
            continue
        title = html.unescape(it["title"]).strip()
        (nid,) = conn.execute(
            """INSERT INTO notices (source_id, url, title, posted_at, organizer, apply_start, apply_end, site_category)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (source_id, it["url"], title, to_date(it.get("posted_at")), it.get("organizer"),
             to_date(it.get("apply_start")), to_date(it.get("apply_end")), it.get("site_category")),
        ).fetchone()
        conn.execute("INSERT INTO notice_links (url, notice_id, source_id, title, site_category) VALUES (%s, %s, %s, %s, %s)",
                     (it["url"], nid, source_id, title, it.get("site_category")))
        new += 1
    return new


# ---------- 다른 사이트의 같은 공고 묶기 ----------

ASK_SIM = 0.4    # 공고명 유사도가 이 이상이면 후보. 공고명이 (괄호·기호 빼고) 같으면 바로 합치고 나머지는 Claude 에게 묻는다

PAIRS_SQL = r"""
SELECT a.id, b.id, a.title, b.title,
       concat_ws(' / ', '주최 ' || a.organizer, '기간 ' || concat(a.apply_start, '~', a.apply_end), '대상 ' || a.target, a.summary),
       concat_ws(' / ', '주최 ' || b.organizer, '기간 ' || concat(b.apply_start, '~', b.apply_end), '대상 ' || b.target, b.summary),
       norm_title(a.title) = norm_title(b.title)
FROM notices a JOIN notices b
  ON a.id < b.id
  -- 후보: 공고명이 비슷하거나, 공고명이 달라도 주최가 비슷하고 마감일이 같은 쌍(현대모비스 로보틱스 ↔ MOBIS Robotics Challenge)
  AND (norm_title(a.title) %% norm_title(b.title)
       OR a.apply_end = b.apply_end AND similarity(a.organizer, b.organizer) >= 0.4)
  -- 같은 게시판의 서로 다른 글은 다른 공고. 씽굿이 다시 올린 '중복 //' 글만 예외
  AND (a.source_id <> b.source_id OR a.title LIKE '중복%%' OR b.title LIKE '중복%%')
WHERE (a.apply_end IS NULL OR b.apply_end IS NULL OR a.apply_end = b.apply_end)
  -- 연도가 둘 다 적혀 있는데 다르면 다른 공고(2022년 ≠ 2026년)
  AND NOT (substring(a.title from '(20\d\d)') IS DISTINCT FROM substring(b.title from '(20\d\d)')
           AND substring(a.title from '(20\d\d)') IS NOT NULL AND substring(b.title from '(20\d\d)') IS NOT NULL)
  AND (a.first_seen_at >= %s OR b.first_seen_at >= %s)
"""

SAME_RULES = """사람이 판단하듯 누가 여는지(주최), 언제 받는지(접수기간), 누구를 받는지(대상), 무슨 사업인지(공고명·내용)를 함께 본다.
- 연도·회차·주최·대상·지역이 다르면 다른 공고다(예: 조선일보 신춘문예 ≠ 문화일보 신춘문예, 대전 사업 ≠ 충북 사업)
- 같은 부처가 같은 기간에 같은 대상으로 내도 사업 이름·내용이 다르면 다른 공고다(예: 정보보호 컨설팅 지원 ≠ 정보보호 제품 도입 지원)
- '모집 공고'와 '선정·합격·결과 안내'는 다른 글이다. 차수·월(9월/12월, 1차/2차)이 다르면 다른 공고다
- 표기만 다르거나 '재공고·연장·수정'이 붙은 것은 같은 공고다. 정보가 비어 있는 칸('?')은 판단에서 뺀다
- 한쪽 공고명에만 [울산] 같은 지역 표시가 붙은 것은 다른 공고라는 근거가 아니다. 영어·한글 이름이 섞여 다르게 적힌 것도 같은 공고일 수 있다
- 접수 시작일은 사이트마다 올린 날을 적는 일이 많아 달라도 근거가 아니다. 주최·마감일·내용이 같으면 공고명이 짧게 줄여 적혔거나 회차가 한쪽에만 있어도 같은 공고다(생활 속 숨은 수학찾기 = 제2회 생활 속 숨어있는 수학 사진 공모전)"""

SAME_SCHEMA = {"type": "object", "properties": {"same": {"type": "array", "items": {"type": "integer"}}}, "required": ["same"]}


def claude_same(pairs):
    """[(번호, 공고A, 공고B)] 중 같은 공고인 번호만 돌려준다."""
    text = "\n".join(f"{i}. A: {a} / B: {b}" for i, a, b in pairs)
    prompt = SAME_RULES + "\n두 사이트에서 모은 공고 쌍이다. 같은 공고인 쌍의 번호만 same 에 넣어라."
    with CLAUDE_SLOTS:
        r = subprocess.run(["claude", "-p", prompt, "--model", "haiku", "--output-format", "json",
                            "--no-session-persistence", "--tools", "", "--json-schema", json.dumps(SAME_SCHEMA)],
                           input=text, capture_output=True, text=True, timeout=300)
    out = json.loads(r.stdout)
    if out.get("is_error") or "structured_output" not in out:
        raise RuntimeError(f"claude 실패: {out.get('result', r.stderr)[:300]}")
    return set(out["structured_output"]["same"])


REGIONS = {"서울": "서울", "부산": "부산", "대구": "대구", "인천": "인천", "광주": "광주", "대전": "대전", "울산": "울산",
           "세종": "세종", "경기": "경기", "강원": "강원", "충북": "충북", "충청북": "충북", "충남": "충남", "충청남": "충남",
           "전북": "전북", "전라북": "전북", "전남": "전남", "전라남": "전남", "경북": "경북", "경상북": "경북",
           "경남": "경남", "경상남": "경남", "제주": "제주"}


def regions_in(t):
    """공고명에 적힌 시·도. 지역마다 따로 하는 같은 이름의 사업을 한 묶음으로 합치지 않으려고 쓴다"""
    return {v for k, v in REGIONS.items() if k in t}


def regions_clash(ta, tb):
    """두 공고명에 모두 시·도가 적혀 있고 서로 다를 때만 다른 공고로 본다. 한쪽에만 [울산] 이 붙은 것은 막지 않는다"""
    ra, rb = regions_in(ta), regions_in(tb)
    return bool(ra and rb and ra != rb)


def merge_duplicates(conn, since):
    """since 이후 들어온 공고를 기존 공고와 비교해 같은 것끼리 하나로 합친다(링크는 모두 남김)."""
    conn.execute(f"SET pg_trgm.similarity_threshold = {ASK_SIM}")
    rows = conn.execute(PAIRS_SQL, (since, since)).fetchall()
    info_title = {}
    for a, b, ta, tb, *_ in rows:
        info_title[a], info_title[b] = ta, tb
    same = [(a, b) for a, b, *_, ident in rows if ident]   # 기호·띄어쓰기만 다른 같은 공고명
    ask = [r for r in rows if not r[-1]]
    batches = [ask[i:i + 50] for i in range(0, len(ask), 50)]
    with ThreadPoolExecutor(PARALLEL) as ex:
        for batch, yes in zip(batches, ex.map(
                lambda bt: claude_same([(i, f"{r[2]} ({r[4] or '?'})", f"{r[3]} ({r[5] or '?'})") for i, r in enumerate(bt)]),
                batches)):
            same += [(batch[i][0], batch[i][1]) for i in yes if i < len(batch)]

    same = [(a, b) for a, b in same if not regions_clash(info_title[a], info_title[b])]
    # 묶음끼리 합칠 때 양쪽에 적힌 시·도가 다르거나, 같은 사이트의 다른 글이 양쪽에 있으면 합치지 않는다
    # (A~B, B~C 로 줄줄이 이어져 다른 공고가 한 묶음이 되는 것을 막는다)
    src = dict(conn.execute("SELECT id, source_id FROM notices WHERE id = ANY(%s)", (list(info_title),)).fetchall())
    info = info_title
    parent, members = {}, {}
    def root(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    def clash(ga, gb):
        return any(regions_clash(info[x], info[y])
                   or src[x] == src[y] and not (info[x].startswith("중복") or info[y].startswith("중복"))
                   for x in ga for y in gb)
    for a, b in sorted(same, key=lambda p: p[0]):
        ra, rb = root(a), root(b)
        if ra == rb:
            continue
        ga, gb = members.get(ra, [ra]), members.get(rb, [rb])
        if clash(ga, gb):
            continue
        keep, gone = min(ra, rb), max(ra, rb)   # 먼저 들어온(번호 작은) 공고를 남긴다
        parent[gone] = keep
        members[keep] = ga + gb
        members.pop(gone, None)
    merged = 0
    for dup in [x for x in parent if root(x) != x]:
        keep = root(dup)
        conn.execute("UPDATE notice_links SET notice_id = %s WHERE notice_id = %s", (keep, dup))
        conn.execute("""UPDATE marks SET notice_id = %s WHERE notice_id = %s
                        AND NOT EXISTS (SELECT 1 FROM marks WHERE notice_id = %s)""", (keep, dup, keep))  # 관심·메모 옮기기
        # 남기는 공고에 빈 칸이 있으면 합쳐지는 공고 값으로 채운다
        conn.execute("""UPDATE notices k SET apply_end = COALESCE(k.apply_end, d.apply_end),
                          apply_start = COALESCE(k.apply_start, d.apply_start),
                          organizer = COALESCE(k.organizer, d.organizer), posted_at = COALESCE(k.posted_at, d.posted_at)
                        FROM notices d WHERE k.id = %s AND d.id = %s""", (keep, dup))
        conn.execute("UPDATE notice_alias SET notice_id = %s WHERE notice_id = %s", (keep, dup))   # 예전에 dup 으로 합쳐진 번호도 keep 으로
        conn.execute("""INSERT INTO notice_alias (old_id, notice_id) VALUES (%s, %s)
                        ON CONFLICT (old_id) DO UPDATE SET notice_id = EXCLUDED.notice_id""", (dup, keep))
        conn.execute("DELETE FROM notices WHERE id = %s", (dup,))
        merged += 1
    # 대표 공고명이 씽굿의 '중복 //' 글이면 다른 사이트의 공고명으로 바꾼다
    conn.execute("""UPDATE notices n SET title = l.title FROM notice_links l
                    WHERE l.notice_id = n.id AND n.title LIKE '중복%%' AND l.title NOT LIKE '중복%%'""")
    return merged


SPLIT_SCHEMA = {"type": "object", "properties": {"wrong": {"type": "array", "items": {"type": "string"}}}, "required": ["wrong"]}


def claude_wrong(groups):
    """[(대표 설명, [(링크 주소, 사이트, 글 제목)])] → 대표 공고와 다른 공고인 링크 주소들"""
    text = "\n\n".join(f"## {g}\n" + "\n".join(f"- {u} [{s}] {t}" for u, s, t in links) for g, links in groups)
    prompt = SAME_RULES + "\n묶음마다 ## 줄이 대표 공고, - 줄이 같은 공고라며 묶인 사이트별 글이다. 대표 공고와 다른 공고인 글의 주소만 wrong 에 넣어라."
    with CLAUDE_SLOTS:
        r = subprocess.run(["claude", "-p", prompt, "--model", "haiku", "--output-format", "json",
                            "--no-session-persistence", "--tools", "", "--json-schema", json.dumps(SPLIT_SCHEMA)],
                           input=text, capture_output=True, text=True, timeout=300)
    out = json.loads(r.stdout)
    if out.get("is_error") or "structured_output" not in out:
        raise RuntimeError(f"claude 실패: {out.get('result', r.stderr)[:300]}")
    return set(out["structured_output"]["wrong"])


def check_groups(conn):
    """이미 묶인 공고를 다시 보고, 다른 공고인 링크는 떼어 새 공고(status new)로 만든다. 뗀 링크 주소들을 돌려준다."""
    rows = conn.execute("""
        SELECT n.id, concat_ws(' / ', n.title, '주최 ' || n.organizer, '기간 ' || concat(n.apply_start, '~', n.apply_end),
                              '대상 ' || n.target, n.summary),
               json_agg(json_build_array(l.url, s.name, l.title) ORDER BY l.url)
        FROM notices n JOIN notice_links l ON l.notice_id = n.id JOIN sources s ON s.id = l.source_id
        GROUP BY n.id HAVING count(*) > 1""").fetchall()
    own = dict(conn.execute("SELECT id, url FROM notices WHERE id = ANY(%s)", ([r[0] for r in rows],)).fetchall())
    batches = [rows[i:i + 20] for i in range(0, len(rows), 20)]
    with ThreadPoolExecutor(PARALLEL) as ex:
        wrong = set().union(*ex.map(lambda bt: claude_wrong([(d, links) for _, d, links in bt]), batches))
    split = []
    for nid, _, links in rows:
        for u, _, t in links:
            if u in wrong and u != own[nid]:   # 대표 공고 자신의 링크는 떼지 않는다
                (sid, cat) = conn.execute("SELECT source_id, site_category FROM notice_links WHERE url = %s", (u,)).fetchone()
                (new,) = conn.execute("INSERT INTO notices (source_id, url, title, site_category) VALUES (%s,%s,%s,%s) RETURNING id",
                                      (sid, u, t, cat)).fetchone()
                conn.execute("UPDATE notice_links SET notice_id = %s WHERE url = %s", (new, u))
                split.append((nid, new, t))
    return split


# ---------- 화면 사이트: 쪽마다 Claude 로 해석 ----------


PARALLEL = 4          # 한꺼번에 부르는 Claude 수
MAX_PAGES = 120       # 무한 루프 막기
CUTOFF = TODAY - timedelta(days=90)  # 마감일 없는 공지는 90일 전 등록분까지


def html_site(url_of, hint, canon, known, *, posted_cutoff=False):
    """1쪽부터 PARALLEL 쪽씩 넘기며 Claude 로 해석한다.
    한 쪽에서 처음 보는 공고가 하나도 없으면(빈 쪽·마지막 쪽 반복·이미 DB 에 있음) 멈춘다."""
    seen, out, page = set(known), [], 1

    def one(n):
        text = page_text(fetch(url_of(n)), url_of(n))
        for attempt in range(2):
            try:
                return claude_list(text, hint)
            except Exception:
                if attempt:
                    raise

    with ThreadPoolExecutor(PARALLEL) as ex:
        while page <= MAX_PAGES:
            pages = list(ex.map(one, range(page, page + PARALLEL)))
            for items in pages:
                fresh = []
                for it in items:
                    it["url"] = canon(it["url"])
                    if it["url"] and it["url"] not in seen:
                        seen.add(it["url"])
                        fresh.append(it)
                if posted_cutoff:
                    fresh = [it for it in fresh if (to_date(it["posted_at"]) or TODAY) >= CUTOFF]
                if not fresh:
                    return out
                out += fresh
            page += PARALLEL
    return out


def canon_by(pattern, template):
    """Claude 가 준 주소에서 공고 번호를 뽑아 늘 같은 꼴의 주소로 만든다(중복 판단이 흔들리지 않게)."""
    def f(url):
        m = re.search(pattern, url or "")
        return template.format(*m.groups()) if m else None
    return f


# ---------- 사이트별 ----------

def s_bizinfo(known):
    return html_site(
        lambda n: f"https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do?schEndAt=N&cpage={n}",
        "링크의 pblancId 값으로 https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId=<값> 을 만든다. 신청기간이 '상시'·'예산 소진시' 같은 글이면 apply_end 는 null",
        canon_by(r"pblancId=(PBLN_\d+)", "https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId={}"),
        known)


def s_kstartup(known):
    canon = canon_by(r"(?:pbancSn=|go_view\(|id=)(\d+)",
                     "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do?schM=view&pbancSn={}")
    hint = "go_view(번호) 의 번호로 https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do?schM=view&pbancSn=<번호> 를 만든다. 위쪽 슬라이더가 아닌 본문 목록만"
    out = []
    for tab in ("PBC010", "PBC020"):  # 공공 / 민간 탭
        out += html_site(lambda n, t=tab: f"https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do?page={n}&pbancClssCd={t}",
                         hint, canon, known | {i["url"] for i in out})
    return out


def s_itp(known):
    canon = canon_by(r"(?:seq=|fncShow\(')(\d+)", "https://www.itp.or.kr/intro.asp?tmid=13&seq={}")
    hint = "fncShow('번호') 의 번호로 https://www.itp.or.kr/intro.asp?tmid=13&seq=<번호> 를 만든다. 목록에 마감일이 없으면 apply_end 는 null, closed 는 false"
    out = []
    for period in (2, 1):  # 진행중, 상시
        out += html_site(lambda n, p=period: f"https://www.itp.or.kr/intro.asp?tmid=13&search_period={p}&PageNum={n}",
                         hint, canon, known | {i["url"] for i in out})
    return out


def s_inuu(known):
    prog = html_site(
        lambda n: f"https://youth.incheon.go.kr/program/programInfoList.do?cate2=all&prgmdiv=inuu&pgno={n}",
        "prgm_seq 값으로 https://youth.incheon.go.kr/program/programInfoDetail.do?prgm_seq=<값>&cate2=all&prgmdiv=inuu 를 만든다. apply_end 는 신청기간 끝날. 상태가 '마감'이면 closed=true",
        canon_by(r"prgm_seq=(\d+)", "https://youth.incheon.go.kr/program/programInfoDetail.do?prgm_seq={}&cate2=all&prgmdiv=inuu"),
        known)
    notice = html_site(
        lambda n: f"https://youth.incheon.go.kr/space/inuu/bbs/bbsMsgList.do?bcd=yic_notice&listsz=50&pgno={n}",
        "msg_seq 값으로 https://youth.incheon.go.kr/space/inuu/bbs/bbsMsgDetail.do?msg_seq=<값>&bcd=yic_notice 를 만든다. posted_at 은 작성일. 목록에 마감일이 없으면 apply_end 는 null, closed 는 false",
        canon_by(r"msg_seq=(\d+)", "https://youth.incheon.go.kr/space/inuu/bbs/bbsMsgDetail.do?msg_seq={}&bcd=yic_notice"),
        known | {i["url"] for i in prog}, posted_cutoff=True)
    return [i for i in prog if not i["closed"]] + notice


def s_thinkyou(known):
    canon = canon_by(r"/(contest|extAct)/(\d+)", "https://thinkyou.co.kr/{}/{}/")
    hint = "href 의 /contest/<번호>/ 또는 /extAct/<번호>/ 앞부분만 써서 https://thinkyou.co.kr/contest/<번호>/ 꼴로 만든다(쿼리 뺌). 기간 'yy-mm-dd ~ yy-mm-dd' 의 뒤가 apply_end"
    out = []
    for st in (0, 1):  # 마감임박, 접수중
        out += html_site(lambda n, s=st: f"https://thinkyou.co.kr/contest/?page={n}&pagesize=45&serstatus={s}",
                         hint, canon, known | {i["url"] for i in out})
    return out


def s_wevity(known):
    canon = canon_by(r"ix=(\d+)", "https://www.wevity.com/?c=find&s=1&gbn=view&ix={}")
    hint = "ix 값으로 https://www.wevity.com/?c=find&s=1&gbn=view&ix=<값> 을 만든다. apply_end 는 D-n 으로 계산. 상태가 접수예정·마감이면 closed=true"
    out = []
    for mode in ("soon", "ing"):
        out += html_site(lambda n, m=mode: f"https://www.wevity.com/?c=find&s=1&mode={m}&gp={n}",
                         hint, canon, known | {i["url"] for i in out})
    return out


def s_mediahub(known):
    return html_site(
        lambda n: f"https://mediahub.seoul.go.kr/competition/competitionListAjax.do?search_contestStatus=proceeding&search_pageNo={n}",
        "href 의 /gongmo/<번호> 로 https://mediahub.seoul.go.kr/gongmo/<번호> 를 만든다. 기간의 뒤가 apply_end. '완료'면 closed=true",
        canon_by(r"/gongmo/(\d+)", "https://mediahub.seoul.go.kr/gongmo/{}"),
        known)


# ---------- 데이터 주소(JSON) 사이트: Claude 없이 ----------

_DETIZEN = {}


def detizen_api(path):
    """대티즌 데이터(Supabase REST). 접속키는 사이트 프로그램 안에 박힌 공개키를 처음 한 번 읽어 온다(바뀌어도 따라감)"""
    if not _DETIZEN:
        home = fetch("https://www.detizen.com/")
        for c in sorted(set(re.findall(r'/_next/static/chunks/[^"\\]+?\.js', home))):
            js = fetch("https://www.detizen.com" + c)
            k = re.search(r"sb_publishable_[A-Za-z0-9_-]+|eyJ[\w-]+\.[\w-]+\.[\w-]+", js)
            b = re.search(r"https://[a-z0-9]+\.supabase\.co", js)
            if k and b:
                _DETIZEN.update(key=k.group(0), base=b.group(0))
                break
        else:
            raise RuntimeError("대티즌 접속키를 못 찾음")
    return fetch_json(f"{_DETIZEN['base']}/rest/v1/{path}", headers={"apikey": _DETIZEN["key"]})


def s_detizen(known):
    t = TODAY.isoformat()
    rows = detizen_api(
        "posts?select=title,slug,type,start_date,end_date,created_at,contest_organizer_name,activity_organizer_name"
        f"&status=eq.published&end_date=gte.{t}&start_date=lte.{t}&order=created_at.desc&limit=1000")
    return [{"title": r["title"], "url": f"https://www.detizen.com/contests/{r['slug']}",
             "posted_at": r["created_at"], "apply_start": r["start_date"], "apply_end": r["end_date"],
             "organizer": r["contest_organizer_name"] or r["activity_organizer_name"], "closed": False}
            for r in rows]


def s_thinkgood(known):
    out, n = [], 1
    while n <= 200:
        req = urllib.request.Request(
            "https://www.thinkcontest.com/thinkgood/user/contest/subList.do",
            data=json.dumps({"currentPageNo": n, "searchProcess": "ING", "sidx": "putup_sdt", "sord": "DESC"}).encode(),
            headers={"User-Agent": UA, "Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.load(r)["listJsonData"]
        for r in rows:
            period = (r.get("receive_period") or "").split("~")
            out.append({"title": r["program_nm"],
                        "url": f"https://www.thinkcontest.com/thinkgood/user/{r.get('reg_type') or 'contest'}/view.do?contest_pk={r['contest_pk']}",
                        "posted_at": r.get("putup_sdt"), "apply_start": period[0], "apply_end": period[-1],
                        "organizer": r.get("host_company"), "closed": False})
        if len(rows) < 10:  # 끝 쪽을 넘기면 마지막 쪽을 다시 주므로 건수로 멈춘다
            break
        n += 1
    return out


def s_ccei(known):
    """창조경제혁신센터 전국 공지. 마감일이 없어 90일 전 등록분까지 받고, 열렸는지는 상세 읽기에서 가린다."""
    out, n = [], 1
    while n <= 50:
        rows = fetch_json("https://ccei.creativekorea.or.kr/incheon/custom/noticeList.json",
                          data={"pn": n, "kind": "all", "pagePerContents": 500})["result"]["list"]
        for r in rows:
            if to_date(r["REG_DATE"]) < CUTOFF:
                return out
            out.append({"title": f"[{r['COUNTRY_NM']}] {r['TITLE']}",
                        "url": f"https://ccei.creativekorea.or.kr/incheon/custom/notice_view.do?no={r['SEQ']}&kind=all",  # kind=all 없으면 다른 센터 글은 빈 화면
                        "posted_at": r["REG_DATE"], "organizer": f"{r['COUNTRY_NM']}창조경제혁신센터", "closed": False})
        if not rows:
            break
        n += 1
    return out


SITES = {
    "기업마당": s_bizinfo,
    "K-Startup": s_kstartup,
    "인천테크노파크": s_itp,
    "인천창조경제혁신센터": s_ccei,
    "인천 청년공간 유유기지": s_inuu,
    "서울 미디어허브": s_mediahub,
    "씽유": s_thinkyou,
    "대티즌": s_detizen,
    "위비티": s_wevity,
    "씽굿": s_thinkgood,
}


def run_source(sid, name):
    with psycopg.connect(DSN, autocommit=True) as conn:
        known = {u for (u,) in conn.execute("SELECT url FROM notice_links WHERE source_id = %s", (sid,))}
        try:
            items = [it for it in SITES[name](known) if is_open(it)]
            new = save(conn, sid, items)
            conn.execute("INSERT INTO runs (source_id, found, new_count) VALUES (%s, %s, %s)", (sid, len(items), new))
            return f"{name}: 열린 것 {len(items)}건, 새로 {new}건"
        except Exception as e:
            conn.execute("INSERT INTO runs (source_id, error) VALUES (%s, %s)", (sid, repr(e)[:1000]))
            return f"{name}: 실패 {e!r}"


def main():
    if sys.argv[1:] == ["--merge-all"]:   # 이미 모은 공고 전체를 한 번 묶을 때
        with psycopg.connect(DSN, autocommit=True) as conn:
            print(f"묶음: {merge_duplicates(conn, date(2000, 1, 1))}건 합침")
        return
    if sys.argv[1:] == ["--check-groups"]:   # 묶인 공고를 다시 보고 다른 공고인 링크를 떼어 냄
        with psycopg.connect(DSN, autocommit=True) as conn:
            for nid, new, t in check_groups(conn):
                print(f"{nid} 에서 뗌 → {new} {t}")
        return
    started = datetime.now().astimezone()
    with psycopg.connect(DSN) as conn:
        rows = conn.execute("SELECT id, name FROM sources WHERE enabled ORDER BY id").fetchall()
    if sys.argv[1:]:
        rows = [r for r in rows if r[1] in sys.argv[1:]]
    with ThreadPoolExecutor(len(rows) or 1) as ex:  # 사이트끼리는 동시에
        for line in ex.map(lambda r: run_source(*r), rows):
            print(line, flush=True)
    with psycopg.connect(DSN, autocommit=True) as conn:
        print(f"묶음: 다른 사이트의 같은 공고 {merge_duplicates(conn, started)}건 합침")


if __name__ == "__main__":
    main()
