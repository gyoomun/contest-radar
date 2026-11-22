"""공고 뷰어: uv run viewer.py → http://127.0.0.1:8765"""
import json
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

from collect import DSN

PORT = 8765

SQL = """
SELECT n.id, n.title, n.url, n.organizer, n.posted_at, n.apply_start, n.apply_end,
       n.status, n.first_seen_at::date, n.is_recruit, n.category, n.summary, n.target, n.target_types,
       n.regions, n.age_min, n.age_max, n.biz_age_max, n.benefit,
       (SELECT json_agg(json_build_object('name', s.name, 'url', l.url) ORDER BY s.name)
        FROM notice_links l JOIN sources s ON s.id = l.source_id WHERE l.notice_id = n.id) AS links
FROM notices n
WHERE n.apply_end IS NULL OR n.apply_end >= CURRENT_DATE
ORDER BY n.apply_end NULLS LAST, n.posted_at DESC NULLS LAST
"""

PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>공고 모아보기</title>
<style>
:root{--bg:#f8f9fa;--card:#fff;--fg:#212529;--mute:#868e96;--line:#e9ecef;--accent:#1c7ed6;--hot:#e03131;--warn:#f08c00}
@media (prefers-color-scheme:dark){:root{--bg:#141517;--card:#1f2023;--fg:#e9ecef;--mute:#909296;--line:#2c2e33;--accent:#4dabf7;--hot:#ff6b6b;--warn:#ffa94d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,"Apple SD Gothic Neo",sans-serif}
header{position:sticky;top:0;background:var(--bg);padding:16px;border-bottom:1px solid var(--line);z-index:1}
h1{font-size:18px;margin:0 0 10px}h1 small{color:var(--mute);font-weight:400;font-size:13px;margin-left:8px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
input[type=search]{flex:1 1 240px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
select{padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
label{color:var(--mute);white-space:nowrap;cursor:pointer}
main{padding:0 16px 40px}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden;margin-top:12px}
th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:12px;color:var(--mute);font-weight:600;background:var(--card);cursor:pointer;user-select:none;white-space:nowrap}
td.t a{color:var(--fg);text-decoration:none;font-weight:500}td.t a:hover{color:var(--accent);text-decoration:underline}
td.m{color:var(--mute);font-size:12px;white-space:nowrap}
.src{text-decoration:none;margin:1px 0;display:inline-block;font-size:11px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);color:var(--mute);white-space:nowrap}
.dd{font-weight:700;white-space:nowrap}.dd.hot{color:var(--hot)}.dd.warn{color:var(--warn)}.dd.none{color:var(--mute);font-weight:400}
.cat{display:inline-block;font-size:11px;padding:0 7px;margin-left:4px;border-radius:99px;background:color-mix(in srgb,var(--accent) 14%,transparent);color:var(--accent);vertical-align:1px}
.sub{color:var(--mute);font-size:12px;margin-top:3px;line-height:1.45}.sub b{font-weight:600;color:var(--fg);opacity:.75;margin-right:4px}.lim{color:var(--warn)}
td.no,th.no{color:var(--mute);font-size:12px;text-align:right;width:1%;white-space:nowrap;font-variant-numeric:tabular-nums}
@media (max-width:700px){.hide-sm{display:none}}
</style></head><body>
<header>
  <h1>공고 모아보기<small id="count"></small></h1>
  <div class="bar">
    <input type="search" id="q" placeholder="공고명·기관 검색 (띄어쓰기로 여러 단어)">
    <select id="src"><option value="">전체 사이트</option></select>
    <select id="cat"><option value="">전체 분야</option></select>
    <select id="tt"><option value="">전체 대상</option></select>
    <select id="rg"><option value="">전체 지역</option></select>
    <label><input type="checkbox" id="soon"> D-7 이내</label>
    <label><input type="checkbox" id="unknown" checked> 마감일 모름 포함</label>
    <label title="출석부·결과 발표·휴관 안내처럼 모집이 아닌 글"><input type="checkbox" id="norec"> 모집 아닌 글도</label>
    <span id="prog" style="color:var(--mute);font-size:12px"></span>
  </div>
</header>
<main><table><thead><tr>
  <th class="no">#</th><th data-k="apply_end">마감</th><th data-k="title">공고명</th><th data-k="organizer" class="hide-sm">기관</th>
  <th class="hide-sm">사이트</th><th data-k="posted_at" class="hide-sm">등록</th>
</tr></thead><tbody id="rows"></tbody></table></main>
<script>
const $=s=>document.querySelector(s), esc=s=>(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let data=[], sortKey='apply_end', asc=true;
const today=new Date(new Date().toDateString());
const dday=d=>d?Math.round((new Date(d)-today)/864e5):null;
const cats=r=>(r.category||[]).length?' '+r.category.map(c=>`<span class="cat">${esc(c)}</span>`).join(''):'';
function info(r){
  if(r.status!=='detailed') return '';
  const lim=[(r.regions||[]).join('·'), r.age_min||r.age_max?`만 ${r.age_min??''}~${r.age_max??''}세`:'', r.biz_age_max?`업력 ${r.biz_age_max}년 이내`:'']
    .filter(Boolean).join(' · ');
  return `<div class="sub">${esc(r.summary)}</div><div class="sub"><b>대상</b> ${esc(r.target)||'-'}${lim?' <span class="lim">('+esc(lim)+')</span>':''}</div>`
    +(r.benefit?`<div class="sub"><b>지원</b> ${esc(r.benefit)}</div>`:'');
}
function render(){
  const words=$('#q').value.trim().toLowerCase().split(/\s+/).filter(Boolean), src=$('#src').value,
    cat=$('#cat').value, tt=$('#tt').value, rg=$('#rg').value;
  let rows=data.filter(r=>(!src||r.links.some(l=>l.name===src))
    &&(r.is_recruit!==false||$('#norec').checked)
    &&(!cat||(r.category||[]).includes(cat))
    &&(!tt||(r.target_types||[]).includes(tt)||(r.target_types||[]).includes('누구나'))
    &&(!rg||(r.regions||[]).includes(rg)||(r.regions||[]).includes('전국'))
    &&(r.apply_end||$('#unknown').checked)
    &&(!$('#soon').checked||(r.apply_end&&dday(r.apply_end)<=7))
    &&words.every(w=>[r.title,r.organizer,r.summary,r.target].join(' ').toLowerCase().includes(w)));
  rows.sort((a,b)=>{const x=a[sortKey]??'',y=b[sortKey]??''; if(!x&&y)return 1; if(x&&!y)return -1; return (x<y?-1:x>y?1:0)*(asc?1:-1)});
  $('#count').textContent=`${rows.length}건 / 전체 ${data.length}건`;
  $('#rows').innerHTML=rows.map((r,i)=>{const d=dday(r.apply_end);
    const cls=d==null?'none':d<=3?'hot':d<=7?'warn':'';
    return `<tr><td class="no">${i+1}</td><td><span class="dd ${cls}">${d==null?'모름':d===0?'오늘':'D-'+d}</span><div style="color:var(--mute);font-size:12px;white-space:nowrap">${r.apply_end||''}</div></td>
      <td class="t"><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>${cats(r)}${info(r)}</td>
      <td class="m hide-sm">${esc(r.organizer)}</td><td class="hide-sm">${[...new Map(r.links.map(l=>[l.name,l])).values()].map(l=>`<a class="src" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.name)}</a>`).join(' ')}</td>
      <td class="m hide-sm">${r.posted_at||''}</td></tr>`}).join('');
}
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k; asc=sortKey===k?!asc:true; sortKey=k; render()});
['#q','#src','#cat','#tt','#rg','#soon','#unknown','#norec'].forEach(s=>$(s).addEventListener('input',render));
fetch('/api/notices').then(r=>r.json()).then(d=>{data=d;
  const opts=(sel,vals)=>[...new Set(vals)].filter(Boolean).sort().forEach(n=>$(sel).insertAdjacentHTML('beforeend',`<option>${esc(n)}</option>`));
  opts('#src',d.flatMap(r=>r.links.map(l=>l.name))); opts('#cat',d.flatMap(r=>r.category||[]));
  opts('#tt',d.flatMap(r=>r.target_types||[])); opts('#rg',d.flatMap(r=>r.regions||[]));
  const done=d.filter(r=>r.status==='detailed').length; $('#prog').textContent=`상세 읽음 ${done}/${d.length}`;
  render()});
</script></body></html>"""

COLS = ["id", "title", "url", "organizer", "posted_at", "apply_start", "apply_end", "status", "first_seen", "is_recruit",
        "category", "summary", "target", "target_types", "regions", "age_min", "age_max", "biz_age_max", "benefit", "links"]


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/notices":
            with psycopg.connect(DSN) as conn:
                rows = [dict(zip(COLS, r)) for r in conn.execute(SQL)]
            body = json.dumps(rows, ensure_ascii=False, default=lambda v: v.isoformat() if isinstance(v, date) else str(v))
            self._send(body, "application/json")
        elif self.path == "/":
            self._send(PAGE, "text/html")
        else:
            self.send_error(404)

    def _send(self, body, ctype):
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
