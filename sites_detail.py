"""사이트별 상세 페이지 읽기: 본문 글과 내려받을 파일(첨부·포스터) 목록을 돌려준다.

get(url) -> (본문 글, [File, ...])
"""
import json
import re
import urllib.parse
from dataclasses import dataclass

from bs4 import BeautifulSoup, Comment

from collect import detizen_api, fetch


@dataclass
class File:
    name: str
    url: str
    post: dict | None = None   # POST 로 받아야 하면 폼 값(위비티)
    b64: bool = False          # 응답이 따옴표로 감싼 base64 JSON 이면 True(씽굿)


def _soup(html):
    s = BeautifulSoup(html, "html.parser")
    for c in s.find_all(string=lambda t: isinstance(t, Comment)):  # 인천테크노파크의 거대한 한글 편집기 주석 등
        c.extract()
    for t in s(["script", "style", "noscript"]):
        t.decompose()
    return s


def _text(*nodes):
    out = []
    for n in nodes:
        if n:
            out.append(re.sub(r"\n\s*\n+", "\n", n.get_text("\n", strip=True)))
    return "\n\n".join(out)


def _abs(base, href):
    return urllib.parse.urljoin(base, href.replace("&amp;", "&"))


def _links(base, soup, pattern, scope=None):
    """scope 안의 a[href] 중 pattern 에 맞는 것 → File(이름=글자 또는 title)"""
    root = soup.select_one(scope) if scope else soup
    files = []
    for a in (root.find_all("a", href=True) if root else []):
        if re.search(pattern, a["href"]):
            name = a.get("title") or a.get_text(" ", strip=True) or "첨부"
            files.append(File(re.sub(r"^\[첨부파일\]\s*", "", name), _abs(base, a["href"])))
    return files


def _imgs(base, soup, selector, skip=r"/images/|logo|icon|banner"):
    return [File(f"포스터{i}{_ext(img['src'])}", _abs(base, img["src"]))
            for i, img in enumerate(soup.select(selector))
            if img.get("src") and not img["src"].startswith("data:") and not re.search(skip, img["src"])]


def _ext(u):
    m = re.search(r"\.(jpe?g|png|gif|webp|pdf|hwpx?)(?:$|\?)", u, re.I)
    return "." + m.group(1).lower() if m else ""


# ---------- 사이트별 ----------

def bizinfo(url, html):
    s = _soup(html)
    return (_text(s.select_one("div.support_project_detail h2.title"), s.select_one("div.view_cont")),
            _links(url, s, r"/cmm/fms/fileDown\.do\?.*downloadToken="))   # 토큰 붙은 href 그대로(없으면 403)


def kstartup(url, html):
    s = _soup(html)
    return (_text(s.select_one("#scrTitle"), s.select_one("div.information_box-wrap"), *s.select("div.information_list")),
            _links(url, s, r"^/afile/fileDownload/"))


def itp(url, html):
    s = _soup(html)
    files = [File(m.group(3), f"https://www.itp.or.kr/common/COM_FILEDOWN.ASP?a={m.group(1)}&b={urllib.parse.quote(m.group(2))}")
             for m in re.finditer(r"fncFileDownload\('([^']+)','([^']+)'\)\"[^>]*title=\"([^\"]+)\"", html)]
    return _text(s.select_one("div.view_area")), files + _imgs(url, s, "div.editor img")


def ccei(url, html):
    s = _soup(html)
    files = []
    for a in s.select('a[href*="fileDown.download?uuid="]'):
        name = a.select_one("span.dwnname")
        files.append(File(name.get_text(strip=True) if name else "첨부", _abs(url, a["href"])))
    return _text(s.select_one("div.vw_article")), files + _imgs(url, s, "div.vw_article img")


def inuu(url, html):
    s = _soup(html)
    if "programInfoDetail" in url:
        return _text(s.select_one("#detail_con div.board_view")), _imgs(url, s, "#detail_con div.center img", skip=r"logo|icon")
    return (_text(s.select_one("#detail_con div.board_view")),
            _links(url, s, r"bbsMsgFileDown\.do", "#detail_con div.add_file"))


def mediahub(url, html):
    s = _soup(html)
    files = [File(a.get("title") or "첨부", _abs(url, a["href"]))
             for a in s.select('a[href*="downloadFile.do?attachNo="]')]
    return _text(s.select_one("div.contest_view")), files + _imgs(url, s, "div.detail div.img img")


def thinkyou(url, html):
    s = _soup(html)
    posters = [File(f"포스터{_ext(i['src'])}", _abs(url, urllib.parse.quote(i["src"], safe="/:%")))
               for i in s.select('img[data-contest-click="poster"]') if i.get("src")]
    return _text(s.select_one("div.contest_outline")), posters[:1]


def wevity(url, html):
    s = _soup(html)
    files = [File(f"첨부{ix}", "https://www.wevity.com/common/modules/filedown_contest.php", post={"ix": ix})
             for ix in re.findall(r"fDown\('(\d+)'\)", html)]
    return (_text(s.select_one("div.contest-detail"), s.select_one("#viewContents")),
            files + _imgs(url, s, '#viewContents img[src*="/upload/contest/"]'))


def thinkgood(url, html):
    s = _soup(html)
    pk = re.search(r"contest_pk=(\d+)", url).group(1)
    files = []
    for a in s.select("a.attechFile"):
        key = a.get("data-file_key")
        u = (f"https://www.thinkcontest.com/thinkgood/common/filedown.do?file_key={key}" if key else
             f"https://www.thinkcontest.com/thinkgood/common/zipfiledown.do?zipfile_nm=x&page_gubun=contest&gubun_fk={pk}")
        files.append(File(a.get_text(strip=True) or "첨부.zip", u, b64=True))
    poster = s.select_one("img.contestimg")
    if poster and poster.get("src"):
        files.append(File("포스터.jpg", _abs(url, poster["src"])))
    return _text(s.select_one("div.content-detail"), s.select_one("#contest_content")), files


def detizen(url, _html):
    slug = urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[1])
    rows = detizen_api(f"posts?slug=eq.{urllib.parse.quote(slug)}&select=*")
    if not rows:
        return "", []
    p = rows[0]
    parts = [f"{k}: {p[k]}" for k in ("title", "contest_organizer_name", "activity_organizer_name", "start_date",
                                      "end_date", "homepage_url") if p.get(k)]
    for k, v in p.items():
        if k.startswith("content_") and v:
            parts.append(f"[{k[8:]}]\n" + BeautifulSoup(v, "html.parser").get_text("\n", strip=True))
    files = [File(f"포스터{_ext(p['thumbnail_key'])}", f"https://images.potionworks.com/{p['thumbnail_key']}")] \
        if p.get("thumbnail_key") else []
    return "\n".join(parts), files


AGGREGATORS = ("wevity.com", "thinkcontest.com", "thinkyou.co.kr", "detizen.com")   # 남의 공고를 옮겨 싣는 모음 사이트
HOMEPAGE_MAX = 15000


def _homepage(html, base, text):
    """모음 사이트 글의 '홈페이지' 칸 링크(주최 쪽 공고 페이지). 모음 사이트 자기 주소는 뺀다"""
    host = urllib.parse.urlsplit(base).hostname
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for label in soup.find_all(string=re.compile(r"^\s*홈페이지\s*$")):
            box = label.parent.parent   # <li><span>홈페이지</span><a> · <div><div class=tit>홈페이지</div><div class=txt><a>
            for a in (box.find_all("a", href=True) if box else []):
                if a["href"].startswith("http") and urllib.parse.urlsplit(a["href"]).hostname != host:
                    return a["href"]
    m = re.search(r"homepage_url: (https?://\S+)", text)   # 대티즌은 API 값
    return m.group(1) if m else None


def _with_homepage(url, html, text):
    """주최 홈페이지 글을 본문 뒤에 붙인다. 모음 사이트 글엔 1등 상금·대상이 빠지고 홈페이지에만 있는 경우가 있다(포항 MICE 해커톤)"""
    hp = _homepage(html, url, text)
    if not hp:
        return text
    try:
        page = _text(_soup(fetch(hp)).body)[:HOMEPAGE_MAX]
    except Exception as e:   # 홈페이지가 막히거나 죽어 있어도 원래 글로는 읽는다
        return text + f"\n\n[주최 홈페이지 {hp}] 열지 못함: {e}"
    return text + f"\n\n[주최 홈페이지 {hp}]\n{page}"


SITES = {
    "bizinfo.go.kr": bizinfo, "k-startup.go.kr": kstartup, "itp.or.kr": itp, "creativekorea.or.kr": ccei,
    "youth.incheon.go.kr": inuu, "mediahub.seoul.go.kr": mediahub, "thinkyou.co.kr": thinkyou,
    "wevity.com": wevity, "thinkcontest.com": thinkgood, "detizen.com": detizen,
}


def get(url):
    host = urllib.parse.urlsplit(url).hostname
    for key, fn in SITES.items():
        if host.endswith(key):
            html = "" if fn is detizen else fetch(url)
            text, files = fn(url, html)
            if not text.strip():
                text = _text(_soup(html).body) if html else ""   # 본문 자리를 못 찾으면 페이지 전체 글
            if key in AGGREGATORS:
                text = _with_homepage(url, html, text)
            return text, files
    raise ValueError(f"모르는 사이트: {url}")
