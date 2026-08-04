"""Static HTML dashboard + markdown digest."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

_CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#16181d;--muted:#5f6672;--card:#fff;
--line:#e4e7ec;--accent:#2b6cb0;--good:#0f7b4f;--warn:#9a5b00}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8ec;--muted:#98a0ad;
--card:#171a21;--line:#262b35;--accent:#7aa7dd;--good:#4fd18b;--warn:#e0a33c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:26px;margin:0 0 4px}
.sub{color:var(--muted);font-size:14px;margin-bottom:20px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:96px}
.stat b{display:block;font-size:20px}
.stat span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
input,select{background:var(--card);color:var(--fg);border:1px solid var(--line);
border-radius:8px;padding:8px 10px;font-size:14px}
input[type=search]{flex:1;min-width:220px}
.job{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px}
.job h2{font-size:16px;margin:0 0 2px}
.job h2 a{color:var(--fg);text-decoration:none}
.job h2 a:hover{color:var(--accent);text-decoration:underline}
.meta{color:var(--muted);font-size:13px;margin-bottom:8px}
.tags{display:flex;gap:6px;flex-wrap:wrap}
.tag{font-size:11.5px;border:1px solid var(--line);border-radius:99px;padding:2px 9px;color:var(--muted)}
.tag.spon{color:var(--good);border-color:currentColor}
.tag.score{color:var(--accent);border-color:currentColor;font-variant-numeric:tabular-nums}
.tag.warn{color:var(--warn);border-color:currentColor}
.why{font-size:13px;color:var(--muted);margin-top:8px;font-style:italic}
.empty{color:var(--muted);padding:40px;text-align:center}
"""

_JS = """
const jobs=window.__JOBS__;const list=document.getElementById('list');
const q=document.getElementById('q'),src=document.getElementById('src'),
      spon=document.getElementById('spon'),sort=document.getElementById('sort');
function pt(j){return j.llm_score!=null?j.llm_score:Math.round(j.score*100)}
function render(){
  let rows=jobs.filter(j=>{
    const t=q.value.toLowerCase();
    if(t&&!(j.title+' '+j.company+' '+j.location).toLowerCase().includes(t))return false;
    if(src.value&&j.source!==src.value)return false;
    if(spon.value==='yes'&&!j.sponsor_matched)return false;
    return true;});
  rows.sort((a,b)=>sort.value==='new'
    ? (b.first_seen||'').localeCompare(a.first_seen||'') : pt(b)-pt(a));
  document.getElementById('shown').textContent=rows.length;
  list.innerHTML=rows.length?rows.map(card).join(''):'<div class="empty">No matches.</div>';
}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function card(j){
  const tags=[`<span class="tag score">${pt(j)}${j.llm_score!=null?' AI':''}</span>`];
  if(j.sponsor_matched)tags.push(`<span class="tag spon">H1B filer · ${j.sponsor_h1b_count}</span>`);
  else tags.push('<span class="tag warn">no H1B history</span>');
  if(j.remote)tags.push('<span class="tag">remote</span>');
  if(j.salary_min)tags.push(`<span class="tag">$${(j.salary_min/1000).toFixed(0)}k+</span>`);
  tags.push(`<span class="tag">${esc(j.source)}</span>`);
  const why=j.llm_reasoning||(j.score_reasons||[]).join(' · ');
  return `<div class="job"><h2><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a></h2>
    <div class="meta">${esc(j.company)}${j.location?' — '+esc(j.location):''}${j.posted_at?' · '+esc(j.posted_at.slice(0,10)):''}</div>
    <div class="tags">${tags.join('')}</div>${why?`<div class="why">${esc(why)}</div>`:''}</div>`;
}
[q,src,spon,sort].forEach(el=>el.addEventListener('input',render));render();
"""


def write_html(jobs: list[dict], out_path: str | Path, stats: dict | None = None) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stats = stats or {}
    sources = sorted({j["source"] for j in jobs})
    sponsored = sum(1 for j in jobs if j.get("sponsor_matched"))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    opts = "".join(f'<option value="{html.escape(s)}">{html.escape(s)}</option>' for s in sources)
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>H1B Job Matches</title><style>{_CSS}</style></head><body><div class="wrap">
<h1>H1B-sponsored matches</h1>
<div class="sub">Generated {generated} · {stats.get('fetched', 0)} postings fetched, {stats.get('new', 0)} new this run</div>
<div class="stats">
  <div class="stat"><b id="shown">{len(jobs)}</b><span>showing</span></div>
  <div class="stat"><b>{len(jobs)}</b><span>matches</span></div>
  <div class="stat"><b>{sponsored}</b><span>H1B filers</span></div>
  <div class="stat"><b>{len(sources)}</b><span>sources</span></div>
</div>
<div class="controls">
  <input type="search" id="q" placeholder="Filter by title, company, location…">
  <select id="src"><option value="">All sources</option>{opts}</select>
  <select id="spon"><option value="">Any employer</option><option value="yes">H1B filers only</option></select>
  <select id="sort"><option value="score">Best match</option><option value="new">Newest</option></select>
</div>
<div id="list"></div></div>
<script>window.__JOBS__={json.dumps(jobs)};</script><script>{_JS}</script>
</body></html>"""
    out_path.write_text(doc, encoding="utf-8")
    return out_path


def write_markdown(jobs: list[dict], out_path: str | Path, top: int = 40) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# H1B job matches — {datetime.now(timezone.utc):%Y-%m-%d}",
        "",
        f"{len(jobs)} matches. Top {min(top, len(jobs))} below.",
        "",
        "| Score | Role | Company | Location | H1B history | Source |",
        "|---:|---|---|---|---|---|",
    ]
    for j in jobs[:top]:
        pts = j["llm_score"] if j.get("llm_score") is not None else round(j["score"] * 100)
        hist = f"{j['sponsor_h1b_count']} approvals" if j.get("sponsor_matched") else "—"
        title = (j["title"] or "").replace("|", "/")[:80]
        lines.append(
            f"| {pts} | [{title}]({j['url']}) | {j['company'].replace('|', '/')} | "
            f"{(j.get('location') or '').replace('|', '/')} | {hist} | {j['source']} |"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
