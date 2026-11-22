"""상세 읽기: 공고 상세 페이지와 첨부(PDF·HWP·HWPX·이미지)를 Claude 가 읽고 칸을 채운다.

uv run detail.py            # 아직 안 읽은 공고 전부
uv run detail.py 20         # 20건만 (시험용)
uv run detail.py --id 123   # 한 건만 다시
"""
import base64
import json
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg

import sites_detail
from collect import DSN, NO_VERIFY, TODAY, UA, to_date

MODEL = "haiku"
PARALLEL = 12   # 동시에 띄우는 claude 수. 6 이면 시간당 200건 남짓
MAX_FILES = 3                  # 공고당 첨부 최대 개수
MAX_BYTES = 20 * 1024 * 1024   # 첨부 하나 최대 크기
MAX_TEXT = 60000               # Claude 에 넘길 글자 수 상한

CATEGORIES = ["창업·사업화", "입주·공간", "R&D·기술", "IT·SW·AI", "아이디어·기획", "디자인", "영상·사진", "글·문학", "미술·예술",
              "음악·공연", "마케팅·광고", "사회·공익", "교육·강연", "대외활동·서포터즈", "채용·인력", "행사·네트워킹", "기타"]
TARGET_TYPES = ["개인", "대학생", "청년", "예비창업자", "창업기업", "중소기업", "소상공인", "대학", "연구기관", "단체·법인", "누구나"]
REGIONS = ["전국", "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남",
           "전북", "전남", "경북", "경남", "제주", "해외"]

S = {"type": ["string", "null"]}
I = {"type": ["integer", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "is_recruit": {"type": "boolean"},
        "organizer": S, "apply_start": S, "apply_end": S, "apply_text": S,
        "category": {"type": "array", "items": {"enum": CATEGORIES}},
        "summary": S, "target": S,
        "target_types": {"type": "array", "items": {"enum": TARGET_TYPES}},
        "age_min": I, "age_max": I,
        "regions": {"type": "array", "items": {"enum": REGIONS}},
        "biz_age_max": {"type": ["number", "null"]},
        "benefit": S, "amount_max": I, "documents": S,
        "top_prize": S, "top_gift": S, "award": S, "top_prize_won": I, "top_gift_won": I,
        "selection": S, "contact": S,
    },
    "required": ["is_recruit", "organizer", "apply_start", "apply_end", "apply_text", "category", "summary", "target",
                 "target_types", "age_min", "age_max", "regions", "biz_age_max", "benefit", "amount_max", "documents",
                 "top_prize", "top_gift", "award", "top_prize_won", "top_gift_won", "selection", "contact"],
}

PRIZE_RULES = """- top_prize: 1등(대상·최우수상 등 가장 높은 상)이 받는 현금 상금. 상 이름과 금액을 짧게 (예: "대상 500만원"). 현금이 없으면 null
- top_gift: 1등이 받는 상품권·물품 (예: "상품권 30만원", "태블릿 PC"). 없으면 null
- award: 가장 높은 상의 상격·상장 (예: "과학기술정보통신부 장관상", "인천광역시장상"). 없으면 null
- top_prize_won, top_gift_won: top_prize 의 현금, top_gift 의 금액(원, 정수). 금액이 안 적혀 있으면 null (예: "대상 500만원" → 5000000, "태블릿 PC" → null)
- 공모전·대회가 아닌 지원사업(사업화 자금·교육 등)이면 모두 null"""

PROMPT = f"""아래는 공고 하나의 상세 페이지 본문과 첨부파일 내용이다. 오늘은 {TODAY}.
그림 파일이 있으면 Read 로 직접 열어 본다(포스터에 접수기간·대상이 적힌 경우가 많다).
본문에 없는 것은 지어내지 말고 null 또는 빈 목록으로 둔다.
- is_recruit: 참가자·참여기업·작품·지원자를 모집하는 공고면 true. 결과·합격자 발표, 출석부, 휴관·일정 안내, 보도자료, 리포트 발간은 false
- organizer: 주최(주관) 기관
- apply_start, apply_end: 접수 시작·마감일 YYYY-MM-DD. 상시·예산 소진 시까지면 apply_end 는 null
- apply_text: 접수기간 원문 그대로 (예: "2026.11.01(토) ~ 11.30(일) 18:00", "상시 접수")
- category: 해당하는 분류 모두. 창업보육센터·공유오피스 등 공간 입주기업·입주자 모집은 "입주·공간"(창업·사업화 아님). 창업·사업화는 자금·멘토링 등을 주는 사업
- summary: 무슨 공고인지 한 줄
- target: 참가 대상·자격 원문 요약 (예: "공고일 기준 업력 7년 이내 창업기업")
- target_types: 지원할 수 있는 사람·기관 종류 모두. 제한 없으면 ["누구나"]
- age_min, age_max: 나이 제한(만 나이), 없으면 null
- regions: 지원 가능한 지역(주소지·소재지 제한). 제한 없으면 ["전국"]
- biz_age_max: 업력 몇 년 이내 제한이면 그 숫자, 없으면 null
- benefit: 지원 내용·시상 요약
- amount_max: 한 곳(한 명)이 받는 최대 금액(원), 모르면 null
- documents: 제출서류
{PRIZE_RULES}
- selection: 선정·심사 방법 요약
- contact: 문의처"""


# ---------- 첨부를 글로 ----------

def file_text(path: Path) -> str | None:
    """글자를 뽑을 수 있으면 글, 못 뽑으면 None(그림·스캔 PDF → Claude 가 직접 본다)"""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            t = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=60).stdout
            return t if len(t.strip()) > 200 else None
        if ext == ".hwp":
            r = subprocess.run(["uv", "tool", "run", "--python", "3.12", "--from", "pyhwp", "--with", "six",
                                "--with", "setuptools<81", "hwp5txt", str(path)],   # pyhwp 가 빠뜨린 의존성
                               capture_output=True, text=True, timeout=120)
            return r.stdout or f"(HWP 변환 실패: {r.stderr[:200]})"
        if ext == ".hwpx":
            with zipfile.ZipFile(path) as z:
                xml = "".join(z.read(n).decode("utf-8", "ignore") for n in sorted(z.namelist())
                              if n.startswith("Contents/section") and n.endswith(".xml"))
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))
        if ext in (".txt", ".csv"):
            return path.read_text(errors="ignore")
    except Exception as e:
        return f"({path.name} 읽기 실패: {e!r})"
    return None


SLOTS = threading.BoundedSemaphore(PARALLEL)

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf")


# ---------- Claude ----------

def claude_detail(text, images, workdir):
    prompt = PROMPT
    tools = []
    if images:
        tools = ["--tools", "Read", "--allowedTools", "Read", "--add-dir", str(workdir)]
        prompt += "\n\n열어 볼 그림·스캔 파일:\n" + "\n".join(str(p) for p in images)
    with SLOTS:
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", MODEL, "--output-format", "json", "--no-session-persistence",
             *(tools or ["--tools", ""]), "--json-schema", json.dumps(SCHEMA)],
            input=text[:MAX_TEXT], capture_output=True, text=True, timeout=600, cwd=workdir)
    out = json.loads(r.stdout)
    if out.get("is_error") or "structured_output" not in out:
        raise RuntimeError(f"claude 실패: {out.get('result', r.stderr)[:300]}")
    return out["structured_output"]


# ---------- 한 건 ----------

def read_one(nid, url, title):
    workdir = Path(tempfile.mkdtemp(prefix=f"notice{nid}-"))
    try:
        body, files = sites_detail.get(url)
        parts = [f"공고명: {title}\n주소: {url}\n\n[본문]\n{body}"]
        images, notes = [], []
        paths = []
        for i, f in enumerate(files):
            paths += download(f, workdir, i)
        for name, path in paths:
            if path is None:
                notes.append(f"{name}: 내려받기 실패")
                continue
            t = file_text(path)
            if t:
                parts.append(f"[첨부: {name}]\n{t}")
            elif path.suffix.lower() in IMAGE_EXT:
                images.append(shrink(path))
        if notes:
            parts.append("[알림]\n" + "\n".join(notes))
        text = "\n\n".join(parts)
        return claude_detail(text, images[:5], workdir), text[:MAX_TEXT]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def download(f, workdir, i):
    """[(이름, 경로|None)]. zip 이면 풀어서 안의 파일들을 돌려준다"""
    if i >= MAX_FILES + 2:   # 첨부 MAX_FILES + 포스터 몇 장
        return []
    ctx = ssl.create_default_context()
    if urllib.parse.urlsplit(f.url).hostname in NO_VERIFY:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        req = urllib.request.Request(f.url, headers={"User-Agent": UA},
                                     data=urllib.parse.urlencode(f.post).encode() if f.post else None)
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            data = r.read(MAX_BYTES + 1)
            cd = r.headers.get("Content-Disposition", "")
        if len(data) > MAX_BYTES:
            return [(f.name, None)]
        if f.b64:
            data = base64.b64decode(json.loads(data))
        ext = Path(f.name).suffix.lower()
        if ext not in (".pdf", ".hwp", ".hwpx", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt"):
            ext = sniff_ext(data, cd)
        path = workdir / f"{i}{ext}"
        path.write_bytes(data)
        if ext == ".zip" and zip_has(path, "Contents/"):   # HWPX 도 zip 꼴이다
            path = path.rename(path.with_suffix(".hwpx"))
        elif ext == ".zip":
            return unzip(path, workdir, i)
        return [(f.name, path)]
    except Exception:
        return [(f.name, None)]


def zip_has(path, prefix):
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.startswith(prefix) for n in z.namelist())
    except zipfile.BadZipFile:
        return False


def unzip(path, workdir, i):
    out = []
    with zipfile.ZipFile(path) as z:
        for j, n in enumerate(z.namelist()[:MAX_FILES]):
            ext = Path(n).suffix.lower()
            if ext in (".pdf", ".hwp", ".hwpx", ".png", ".jpg", ".jpeg", ".txt"):
                p = workdir / f"{i}_{j}{ext}"
                p.write_bytes(z.read(n))
                out.append((n, p))
    return out


def shrink(path):
    """아주 큰 포스터(6000px 등)는 줄여서 넘긴다"""
    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        subprocess.run(["sips", "-Z", "2000", str(path)], capture_output=True)
    return path


def sniff_ext(data, cd):
    raw = cd.encode("latin-1", "ignore")
    m = re.search(rb'filename\*?=(?:UTF-8\'\')?"?([^";]+)', raw)
    if m:
        name = m.group(1)
        for dec in (lambda b: urllib.parse.unquote(b.decode()), lambda b: b.decode("utf-8"), lambda b: b.decode("cp949")):
            try:
                e = Path(dec(name)).suffix.lower()
                if e:
                    return e
            except Exception:
                pass
    for sig, ext in ((b"%PDF", ".pdf"), (b"\x89PNG", ".png"), (b"\xff\xd8", ".jpg"), (b"GIF8", ".gif"),
                     (b"\xd0\xcf\x11\xe0", ".hwp"), (b"PK", ".zip")):
        if data.startswith(sig):
            return ext
    return ".bin"


# ---------- DB ----------

def save(conn, nid, d, text):
    end = to_date(d["apply_end"])
    conn.execute(
        """UPDATE notices SET status = 'detailed', error = NULL,
             is_recruit = %s, organizer = COALESCE(%s, organizer),
             apply_start = COALESCE(%s, apply_start), apply_end = COALESCE(%s, apply_end),
             category = %s, summary = %s, target = %s, target_types = %s, age_min = %s, age_max = %s,
             regions = %s, biz_age_max = %s, benefit = %s, amount_max = %s, documents = %s,
             top_prize = %s, top_gift = %s, award = %s, top_prize_won = %s, top_gift_won = %s, detail = %s, detail_text = %s
           WHERE id = %s""",
        (d["is_recruit"], d["organizer"], to_date(d["apply_start"]), end,
         d["category"], d["summary"], d["target"], d["target_types"], d["age_min"], d["age_max"],
         d["regions"], d["biz_age_max"], d["benefit"], d["amount_max"], d["documents"], d["top_prize"], d["top_gift"], d["award"], d["top_prize_won"], d["top_gift_won"],
         json.dumps({k: d[k] for k in ("apply_text", "selection", "contact")}, ensure_ascii=False), text, nid))


def work(row):
    nid, url, title = row
    with psycopg.connect(DSN, autocommit=True) as conn:
        try:
            d, text = read_one(nid, url, title)
            save(conn, nid, d, text)
            return f"{nid} 완료 {title[:40]}"
        except Exception as e:
            conn.execute("UPDATE notices SET status = 'failed', error = %s WHERE id = %s", (repr(e)[:1000], nid))
            return f"{nid} 실패 {e!r}"[:200]


def main():
    args = sys.argv[1:]
    with psycopg.connect(DSN) as conn:
        if args[:1] == ["--id"]:
            rows = conn.execute("SELECT id, url, title FROM notices WHERE id = %s", (int(args[1]),)).fetchall()
        else:
            limit = int(args[0]) if args else None
            rows = conn.execute("SELECT id, url, title FROM notices WHERE status IN ('new', 'failed') "
                                "ORDER BY apply_end NULLS FIRST, id LIMIT %s", (limit,)).fetchall()
    print(f"{len(rows)}건 읽기 시작", flush=True)
    with ThreadPoolExecutor(PARALLEL) as ex:
        for line in ex.map(work, rows):
            print(line, flush=True)


if __name__ == "__main__":
    main()
