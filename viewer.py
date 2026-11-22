"""공고 뷰어: uv run viewer.py → http://127.0.0.1:8765"""
import json
import os
import subprocess
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

from collect import DSN

PORT = 8765
LOCK = "/tmp/contest-radar.lock"   # daily.sh 가 도는 동안 있는 폴더
DAILY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily.sh")

# 사이트별 마지막 실행이 실패했거나, 7일 넘게 돌았는데 새 글이 한 번도 없으면 알린다
STATUS_SQL = """
SELECT s.name, last.error, last.ran_at,
       NOT EXISTS (SELECT 1 FROM runs r WHERE r.source_id = s.id AND r.new_count > 0 AND r.ran_at > now() - interval '7 days')
       AND (SELECT min(ran_at) FROM runs r WHERE r.source_id = s.id) < now() - interval '7 days'
FROM sources s
CROSS JOIN LATERAL (SELECT error, ran_at FROM runs r WHERE r.source_id = s.id ORDER BY ran_at DESC LIMIT 1) last
WHERE s.enabled ORDER BY s.id
"""

SQL = """
SELECT n.id, n.title, n.url, n.organizer, n.posted_at, n.apply_start, n.apply_end,
       n.status, n.first_seen_at::date, n.is_recruit, n.category, n.summary, n.target, n.target_types,
       n.regions, n.age_min, n.age_max, n.biz_age_max, n.benefit,
       n.first_seen_at > now() - interval '24 hours', m.star, m.memo,
       (SELECT json_agg(json_build_object('name', s.name, 'url', l.url) ORDER BY s.name)
        FROM notice_links l JOIN sources s ON s.id = l.source_id WHERE l.notice_id = n.id) AS links
FROM notices n LEFT JOIN marks m ON m.notice_id = n.id
WHERE n.apply_end IS NULL OR n.apply_end >= CURRENT_DATE
ORDER BY n.apply_end NULLS LAST, n.posted_at DESC NULLS LAST
"""

PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>공고 모아보기</title>
<style>
:root{--bg:#f8f9fa;--card:#fff;--fg:#212529;--mute:#868e96;--line:#e9ecef;--accent:#1c7ed6;--hot:#e03131;--warn:#f08c00}
@media (prefers-color-scheme:dark){:root{--bg:#141517;--card:#1f2023;--fg:#e9ecef;--mute:#909296;--line:#2c2e33;--accent:#4dabf7;--hot:#ff6b6b;--warn:#ffa94d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,"Apple SD Gothic Neo",sans-serif}
header{position:sticky;top:0;background:var(--bg);padding:12px 8px;border-bottom:1px solid var(--line);z-index:1}
h1{font-size:22px;margin:0 0 10px}h1 small{color:var(--mute);font-weight:400;font-size:15px;margin-left:8px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
input[type=search]{flex:1 1 240px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
details.ms{position:relative}details.ms>summary::-webkit-details-marker{display:none}details.ms>summary{list-style:none;cursor:pointer;white-space:nowrap;min-width:130px;appearance:none;padding:8px 32px 8px 10px;background:var(--card) url("data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%2712%27 height=%278%27%3E%3Cpath d=%27M1 1l5 5 5-5%27 fill=%27none%27 stroke=%27%23868e96%27 stroke-width=%271.6%27/%3E%3C/svg%3E") no-repeat right 12px center;border:1px solid var(--line);border-radius:8px;color:var(--fg);font:inherit}
details.ms .pop{position:absolute;z-index:5;top:calc(100% + 4px);left:0;min-width:100%;max-height:60vh;overflow:auto;padding:6px;background:var(--card);border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.35)}details.ms .pop label{display:flex;gap:8px;align-items:center;padding:6px 8px;border-radius:6px;white-space:nowrap;cursor:pointer;font-size:15px}details.ms .pop label:hover{background:var(--line)}details.ms[data-n]:not([data-n="0"])>summary{border-color:var(--accent);color:var(--accent)}
label.tg{color:var(--mute);white-space:nowrap;cursor:pointer;padding:7px 12px;border:1px solid var(--line);border-radius:99px;font-size:14px;user-select:none}label.tg input{display:none}label.tg:has(input:checked){color:var(--accent);border-color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,transparent)}input[type=search]{flex:1 1 200px;max-width:360px}
main{padding:0 8px 40px;overflow-x:auto}td.id{color:var(--mute);font-size:14px;line-height:25px;vertical-align:middle;font-variant-numeric:tabular-nums;font-family:ui-monospace,Menlo,monospace;width:1px;white-space:nowrap}
table{width:100%;min-width:1280px;table-layout:fixed;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden;margin-top:12px}
th,td{padding:9px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:14px;color:var(--mute);font-weight:600;background:var(--card);cursor:pointer;user-select:none;white-space:nowrap}
td.org{min-width:150px}th.c,td.stc{text-align:center;width:1px}td.no,td.stc{vertical-align:middle}td.tags,td.site,td.date{width:1px;white-space:nowrap}td.tags>*,td.site .src{display:block;width:fit-content;margin:0 0 6px}td.tags>:last-child,td.site .src:last-child{margin-bottom:0}td.t a{color:var(--fg);text-decoration:none;font-weight:500}td.t a:hover{color:var(--accent);text-decoration:underline}
td.m{color:var(--mute);font-size:14px}td.c{font-size:14px;line-height:1.5}
.src{text-decoration:none;margin:1px 0;display:inline-block;font-size:13px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);color:var(--mute);white-space:nowrap}
.dd{font-weight:700;white-space:nowrap}.dd.hot{color:var(--hot)}.dd.warn{color:var(--warn)}.dd.none{color:var(--mute);font-weight:400}
.cat{display:inline-block;font-size:13px;padding:0 7px;margin:0 0 3px;white-space:nowrap;border-radius:99px;background:color-mix(in srgb,var(--accent) 14%,transparent);color:var(--accent);vertical-align:1px}
.sub{color:var(--mute);font-size:14px;margin-top:4px;line-height:1.5}.sub b{font-weight:600;color:var(--fg);opacity:.75;margin-right:4px}.lim{color:var(--warn)}
.new{display:inline-block;font-size:13px;font-weight:700;padding:0 7px;margin:0 0 3px;border-radius:99px;background:color-mix(in srgb,var(--hot) 16%,transparent);color:var(--hot)}
.st{background:none;border:0;cursor:pointer;font-size:22px;line-height:25px;height:25px;color:var(--mute);padding:0 4px;border-radius:6px}.st:hover{background:var(--line)}.st.on{color:#fab005}
.memo{font-size:14px;margin-top:4px;padding:3px 8px;border-radius:6px;background:color-mix(in srgb,#fab005 16%,transparent);white-space:pre-wrap}
.mbtn{font-size:13px;color:var(--mute);cursor:pointer;margin-left:6px;opacity:0;text-decoration:underline}tr:hover .mbtn{opacity:1}
#warn{margin:8px 0 0;padding:8px 12px;border-radius:8px;background:color-mix(in srgb,var(--hot) 12%,transparent);color:var(--hot);font-size:15px}#warn:empty{display:none}
.run{margin-left:auto;padding:7px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit;cursor:pointer}.run:disabled{opacity:.5;cursor:default}
/* 열 폭 고정: 검색·새로고침 때 내용에 따라 폭이 다시 계산돼 헤더가 움찔거리지 않게. 공고명이 남는 폭을 가진다 */
thead th:nth-child(1){width:40px}thead th:nth-child(2){width:48px}thead th:nth-child(3){width:40px}thead th:nth-child(4){width:92px}thead th:nth-child(5){width:118px}thead th:nth-child(7){width:16%}thead th:nth-child(8){width:15%}thead th:nth-child(9){width:140px}thead th:nth-child(10){width:152px}thead th:nth-child(11){width:96px}td{overflow-wrap:anywhere}
td.no,th.no{padding-left:2px;padding-right:2px;color:var(--mute);font-size:14px;line-height:25px;text-align:center;width:1%;white-space:nowrap;font-variant-numeric:tabular-nums}
@media (max-width:700px){.hide-sm{display:none}}
</style></head><body>
<header>
  <div class="bar"><h1 style="margin:0">공고 모아보기<small id="count"></small><small id="prog" style="margin-left:0"></small></h1>
    <span id="last" style="margin-left:auto;color:var(--mute);font-size:14px"></span>
    <button class="run" id="run" style="margin-left:0">지금 수집</button></div>
  <div id="warn"></div>
  <div style="height:10px"></div>
  <div class="bar">
    <input type="search" id="q" placeholder="공고명·기관·메모·ID 검색">
    <details class="ms" id="src" data-t="사이트"><summary>전체 사이트</summary><div class="pop"></div></details>
    <details class="ms" id="cat" data-t="분야"><summary>전체 분야</summary><div class="pop"></div></details>
    <details class="ms" id="tt" data-t="대상"><summary>전체 대상</summary><div class="pop"></div></details>
    <details class="ms" id="rg" data-t="지역"><summary>전체 지역</summary><div class="pop"></div></details>
    <label class="tg"><input type="checkbox" id="star">★ 관심만</label>
    <label class="tg"><input type="checkbox" id="onlynew">NEW만</label>
    <label class="tg"><input type="checkbox" id="soon">D-7 이내</label>
    <label class="tg"><input type="checkbox" id="unknown" checked>마감일 모름 포함</label>
    <label class="tg" title="출석부·결과 발표·휴관 안내처럼 모집이 아닌 글"><input type="checkbox" id="norec">모집 아닌 글도</label>
  </div>
</header>
<main><table><thead><tr>
  <th class="no">#</th><th data-k="id">ID</th><th data-k="star" class="c">★</th><th data-k="apply_end">마감</th><th>분야</th><th data-k="title">공고명</th><th>대상</th><th>지원</th><th data-k="organizer" class="hide-sm">기관</th>
  <th class="hide-sm">사이트</th><th data-k="posted_at" class="hide-sm">등록</th>
</tr></thead><tbody id="rows"></tbody></table></main>
<script>
const $=s=>document.querySelector(s), esc=s=>(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let data=[], sortKey='apply_end', asc=true;
const today=new Date(new Date().toDateString());
const dday=d=>d?Math.round((new Date(d)-today)/864e5):null;
const cats=r=>(r.category||[]).map(c=>`<span class="cat">${esc(c)}</span>`).join('');
function lim(r){
  if(r.status!=='detailed') return '';
  const lim=[(r.regions||[]).join('·'), r.age_min||r.age_max?`만 ${r.age_min??''}~${r.age_max??''}세`:'', r.biz_age_max?`업력 ${r.biz_age_max}년 이내`:'']
    .filter(Boolean).join(' · ');
  return lim?`<div class="lim" style="margin-top:4px">${esc(lim)}</div>`:'';
}
// 여러 개 고르기: 고른 값 목록. 아무것도 안 고르면 전체
const picked=sel=>[...document.querySelectorAll(sel+' input:checked')].map(i=>i.value);
document.querySelectorAll('details.ms').forEach(d=>d.addEventListener('change',()=>{
  const v=picked('#'+d.id); d.dataset.n=v.length;
  d.querySelector('summary').textContent=!v.length?'전체 '+d.dataset.t:v.length===1?v[0]:`${d.dataset.t} ${v.length}개`; render()}));
document.addEventListener('click',e=>document.querySelectorAll('details.ms[open]').forEach(d=>{if(!d.contains(e.target))d.open=false}));
function render(){
  const words=$('#q').value.trim().toLowerCase().split(/\s+/).filter(Boolean), src=picked('#src'),
    cat=picked('#cat'), tt=picked('#tt'), rg=picked('#rg'), any=(have,want)=>!want.length||(have||[]).some(v=>want.includes(v));
  let rows=data.filter(r=>any(r.links.map(l=>l.name),src)
    &&(r.is_recruit!==false||$('#norec').checked)
    &&(!$('#star').checked||r.star)&&(!$('#onlynew').checked||r.is_new)
    &&any(r.category,cat)
    &&(any(r.target_types,tt)||(tt.length&&(r.target_types||[]).includes('누구나')))
    &&(any(r.regions,rg)||(rg.length&&(r.regions||[]).includes('전국')))
    &&(r.apply_end||$('#unknown').checked)
    &&(!$('#soon').checked||(r.apply_end&&dday(r.apply_end)<=7))
    &&words.every(w=>w===String(r.id)||[r.title,r.organizer,r.summary,r.target,r.memo].join(' ').toLowerCase().includes(w)));
  rows.sort((a,b)=>{const x=a[sortKey]??'',y=b[sortKey]??''; if(!x&&y)return 1; if(x&&!y)return -1; return (x<y?-1:x>y?1:0)*(asc?1:-1)});
  $('#count').textContent=`${rows.length}건 / 전체 ${data.length}건`;
  $('#rows').innerHTML=rows.map((r,i)=>{const d=dday(r.apply_end);
    const cls=d==null?'none':d<=3?'hot':d<=7?'warn':'';
    return `<tr data-id="${r.id}"><td class="no">${i+1}</td><td class="id">${r.id}</td><td class="stc"><button class="st${r.star?' on':''}" data-act="star" title="관심">${r.star?'★':'☆'}</button></td><td><span class="dd ${cls}">${d==null?'모름':d===0?'오늘':'D-'+d}</span><div style="color:var(--mute);font-size:14px;white-space:nowrap">${r.apply_end||''}</div></td>
      <td class="tags">${r.is_new?'<span class="new">NEW</span>':''}${cats(r)}</td>
      <td class="t"><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a><span class="mbtn" data-act="memo">${r.memo?'메모 고치기':'메모'}</span>${r.summary?`<div class="sub">${esc(r.summary)}</div>`:''}${r.memo?`<div class="memo">${esc(r.memo)}</div>`:''}</td>
      <td class="c">${esc(r.target)}${lim(r)}</td><td class="c">${esc(r.benefit)}</td>
      <td class="m hide-sm org">${esc(r.organizer)}</td><td class="hide-sm site">${[...new Map(r.links.map(l=>[l.name,l])).values()].map(l=>`<a class="src" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.name)}</a>`).join(' ')}</td>
      <td class="m hide-sm date">${r.posted_at||''}</td></tr>`}).join('');
}
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k; asc=sortKey===k?!asc:true; sortKey=k; render()});
['#q','#star','#onlynew','#soon','#unknown','#norec'].forEach(s=>$(s).addEventListener('input',render));
const save=r=>fetch('/api/mark',{method:'POST',body:JSON.stringify({id:r.id,star:r.star,memo:r.memo})});
$('#rows').addEventListener('click',e=>{const act=e.target.dataset.act; if(!act)return;
  const r=data.find(x=>x.id==e.target.closest('tr').dataset.id);
  if(act==='star') r.star=!r.star;
  else {const m=prompt('메모 (비우면 지움)',r.memo||''); if(m===null)return; r.memo=m.trim()||null;}
  save(r); render()});
let wasRunning=false;
function status(){fetch('/api/status').then(r=>r.json()).then(s=>{
  $('#last').textContent=s.last_run?'마지막 수집 '+s.last_run.slice(5,16):'';
  $('#run').disabled=s.running; $('#run').textContent=s.running?'수집 중…':'지금 수집';
  $('#warn').textContent=[s.failed.length?'수집 실패: '+s.failed.join(', '):'', s.quiet.length?'7일째 새 글 없음(사이트 구조가 바뀌었을 수 있음): '+s.quiet.join(', '):''].filter(Boolean).join(' · ');
  if(wasRunning&&!s.running) location.reload();   // 수집이 끝나면 새 공고를 다시 불러온다
  wasRunning=s.running; setTimeout(status,s.running?10000:60000)})}
$('#run').onclick=()=>{if(!confirm('지금 수집을 시작할까요? (몇 분~수십 분 걸립니다)'))return;
  fetch('/api/run',{method:'POST'}).then(()=>{$('#run').disabled=true; $('#run').textContent='수집 중…'; setTimeout(status,3000)})};
status();
fetch('/api/notices').then(r=>r.json()).then(d=>{data=d;
  const opts=(sel,vals)=>[...new Set(vals)].filter(Boolean).sort().forEach(n=>$(sel+' .pop').insertAdjacentHTML('beforeend',`<label><input type="checkbox" value="${esc(n)}">${esc(n)}</label>`));
  opts('#src',d.flatMap(r=>r.links.map(l=>l.name))); opts('#cat',d.flatMap(r=>r.category||[]));
  opts('#tt',d.flatMap(r=>r.target_types||[])); opts('#rg',d.flatMap(r=>r.regions||[]));
  const done=d.filter(r=>r.status==='detailed').length; $('#prog').textContent=done<d.length?` · 상세 읽음 ${done}/${d.length}`:'';
  render()});
</script></body></html>"""

COLS = ["id", "title", "url", "organizer", "posted_at", "apply_start", "apply_end", "status", "first_seen", "is_recruit",
        "category", "summary", "target", "target_types", "regions", "age_min", "age_max", "biz_age_max", "benefit",
        "is_new", "star", "memo", "links"]


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/notices":
            with psycopg.connect(DSN) as conn:
                rows = [dict(zip(COLS, r)) for r in conn.execute(SQL)]
            body = json.dumps(rows, ensure_ascii=False, default=lambda v: v.isoformat() if isinstance(v, date) else str(v))
            self._send(body, "application/json")
        elif self.path == "/api/status":
            with psycopg.connect(DSN) as conn:
                rows = conn.execute(STATUS_SQL).fetchall()
            self._send(json.dumps({
                "running": os.path.isdir(LOCK),
                "last_run": max((r[2] for r in rows), default=None),
                "failed": [r[0] for r in rows if r[1]],
                "quiet": [r[0] for r in rows if r[3]],
            }, ensure_ascii=False, default=str), "application/json")
        elif self.path == "/":
            self._send(PAGE, "text/html")
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/mark":
            with psycopg.connect(DSN) as conn:
                conn.execute("""INSERT INTO marks (notice_id, star, memo) VALUES (%(id)s, %(star)s, %(memo)s)
                                ON CONFLICT (notice_id) DO UPDATE SET star = EXCLUDED.star, memo = EXCLUDED.memo, updated_at = now()""",
                             {"id": int(body["id"]), "star": bool(body.get("star")), "memo": body.get("memo") or None})
            self._send("{}", "application/json")
        elif self.path == "/api/run":
            if not os.path.isdir(LOCK):   # 이미 돌고 있으면 그대로 둔다(daily.sh 도 잠금으로 막는다)
                subprocess.Popen([DAILY, "--force"], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._send("{}", "application/json")
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
