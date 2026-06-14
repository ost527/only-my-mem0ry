#!/usr/bin/env python3
"""
build_memory_viewer.py — generate a self-contained, READ-ONLY HTML card view of
your local-mem0-mcp memories.

It reads the Chroma store + the pin/usage sidecar (memory_meta.json) DIRECTLY:
no embedding model, no LLM, and no running MCP server are needed. It writes ONE
standalone .html file (CSS + JS + data all inline, zero network calls) with
client-side search / sort / filter. Double-click the file to browse.

Usage:
    .venv/bin/python server/build_memory_viewer.py           # -> ~/.mem0-mcp/memory-viewer.html
    .venv/bin/python server/build_memory_viewer.py --open    # ...and open it in your browser

Options mirror the server env vars (MEM0_CHROMA_PATH, MEM0_COLLECTION,
MEM0_DEFAULT_USER, MEM0_META_FILE).
"""
import os
import json
import argparse
import webbrowser
from datetime import datetime

from mem0_store import expand as _expand, load_meta as _load_sidecar


def load_memories(chroma_path: str, collection: str, user: str) -> list:
    """All memories for `user` straight from Chroma metadata (text lives in
    metadata['data']; Chroma 'documents' is None for mem0)."""
    import chromadb

    client = chromadb.PersistentClient(path=chroma_path)
    col = client.get_collection(collection)
    res = col.get(include=["metadatas"])  # no limit => all records
    ids = res.get("ids") or []
    metas = res.get("metadatas") or []
    out = []
    for mid, meta in zip(ids, metas):
        meta = meta or {}
        if user and meta.get("user_id") not in (None, user):
            continue
        out.append({
            "id": mid,
            "text": meta.get("data", "") or "",
            "created": meta.get("created_at"),
            "updated": meta.get("updated_at"),
        })
    return out


def load_meta(meta_path: str):
    """Return (pinned:set, access:dict{id:{count,last}}) from the sidecar."""
    meta = _load_sidecar(meta_path)
    return set(meta.get("pinned") or []), (meta.get("access") or {})


def build_payload(memories: list, pinned: set, access: dict, user: str) -> dict:
    records = []
    for r in memories:
        st = access.get(r["id"]) or {}
        records.append({
            "id": r["id"],
            "text": r["text"],
            "created": r["created"],
            "updated": r["updated"],
            "pinned": r["id"] in pinned,
            "count": int(st.get("count", 0) or 0),
            "last": st.get("last"),
        })
    # Default order: newest first (the viewer re-sorts client-side anyway).
    records.sort(key=lambda x: (x["created"] or ""), reverse=True)
    return {
        "generated": datetime.now().astimezone().isoformat(),
        "user": user,
        "total": len(records),
        "pinnedCount": sum(1 for r in records if r["pinned"]),
        "memories": records,
    }


# --- HTML template (no .format/f-string: contains many { } and JS) ------------
# Data is injected by replacing the __DATA_JSON__ marker inside a JSON script tag.
TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mem0 메모리 뷰어</title>
<style>
  :root{
    --bg:#f4f5f7; --panel:#ffffff; --ink:#1d2330; --muted:#6b7280; --line:#e3e6ea;
    --accent:#3b6ef5; --pin:#e0a400; --chip:#eef1f6; --mark:#fff2a8;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#0e1116; --panel:#171b22; --ink:#e6e9ef; --muted:#9aa3b2; --line:#262c36;
      --accent:#6c93ff; --pin:#f0c548; --chip:#222834; --mark:#5e5320;
    }
  }
  *{box-sizing:border-box}
  body{margin:0;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Apple SD Gothic Neo",sans-serif;
       background:var(--bg);color:var(--ink)}
  header{position:sticky;top:0;z-index:5;background:var(--panel);border-bottom:1px solid var(--line);
         padding:14px 20px;backdrop-filter:saturate(1.2) blur(4px)}
  .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  h1{font-size:16px;margin:0 12px 0 0;font-weight:700;letter-spacing:.2px}
  .stat{color:var(--muted);font-size:13px}
  input[type=search],select{font:inherit;color:var(--ink);background:var(--bg);
       border:1px solid var(--line);border-radius:9px;padding:8px 11px;outline:none}
  input[type=search]{min-width:240px;flex:1 1 240px}
  input[type=search]:focus,select:focus{border-color:var(--accent)}
  label.chk{display:inline-flex;gap:6px;align-items:center;color:var(--muted);font-size:13px;user-select:none;cursor:pointer}
  main{padding:20px;max-width:1500px;margin:0 auto}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 15px;
        display:flex;flex-direction:column;gap:10px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
  .card.pin{border-color:var(--pin);box-shadow:0 0 0 1px var(--pin) inset}
  .badges{display:flex;gap:6px;flex-wrap:wrap;min-height:0}
  .chip{font-size:11px;color:var(--muted);background:var(--chip);border-radius:999px;padding:2px 9px;font-weight:600}
  .chip.pin{color:#7a5a00;background:rgba(224,164,0,.18)}
  .body{white-space:pre-wrap;word-break:break-word;margin:0}
  .body.clamp{max-height:210px;overflow:hidden;-webkit-mask-image:linear-gradient(#000 75%,transparent)}
  .more{align-self:flex-start;font:inherit;font-size:12px;color:var(--accent);background:none;border:none;
        padding:0;cursor:pointer}
  .meta{margin-top:auto;border-top:1px dashed var(--line);padding-top:9px;display:flex;flex-wrap:wrap;
        gap:4px 14px;color:var(--muted);font-size:11.5px}
  .id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px}
  .id button{font:inherit;font-size:10px;border:1px solid var(--line);background:var(--bg);color:var(--muted);
        border-radius:6px;padding:1px 6px;margin-left:6px;cursor:pointer}
  mark{background:var(--mark);color:inherit;border-radius:3px;padding:0 1px}
  .empty{color:var(--muted);text-align:center;padding:60px 0}
  footer{color:var(--muted);font-size:12px;text-align:center;padding:18px}
</style>
</head>
<body>
<header>
  <div class="row">
    <h1>🧠 Mem0 메모리</h1>
    <input id="q" type="search" placeholder="검색 (내용·ID)…" autocomplete="off">
    <select id="sort">
      <option value="new">최신순</option>
      <option value="old">오래된순</option>
      <option value="upd">최근 수정순</option>
      <option value="used">많이 조회순</option>
      <option value="pin">핀 우선</option>
    </select>
    <label class="chk"><input id="pinonly" type="checkbox"> 📌 핀만</label>
    <span class="stat" id="count"></span>
  </div>
</header>
<main>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" hidden>일치하는 메모리가 없습니다.</div>
</main>
<footer id="foot"></footer>

<script id="payload" type="application/json">__DATA_JSON__</script>
<script>
(function(){
  var DATA = JSON.parse(document.getElementById('payload').textContent);
  var grid = document.getElementById('grid');
  var emptyEl = document.getElementById('empty');
  var countEl = document.getElementById('count');
  var qEl = document.getElementById('q');
  var sortEl = document.getElementById('sort');
  var pinEl = document.getElementById('pinonly');

  document.getElementById('foot').textContent =
    '전체 ' + DATA.total + '개 · 핀 ' + DATA.pinnedCount + '개 · 사용자 ' + DATA.user +
    ' · 생성 ' + fmtDate(DATA.generated);

  function fmtDate(iso){
    if(!iso) return '—';
    var d = new Date(iso); if(isNaN(d.getTime())) return '—';
    var p = function(n){ return String(n).padStart(2,'0'); };
    return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes());
  }
  function fmtDay(s){ return s ? String(s).slice(0,10) : '—'; }
  function esc(s){ return s.replace(/[&<>]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]; }); }
  function highlight(text, q){
    var safe = esc(text);
    if(!q) return safe;
    var rx;
    try{ rx = new RegExp('('+q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','gi'); }
    catch(e){ return safe; }
    return safe.replace(rx, '<mark>$1</mark>');
  }

  function render(){
    var q = qEl.value.trim().toLowerCase();
    var sort = sortEl.value;
    var pinOnly = pinEl.checked;
    var items = DATA.memories.filter(function(m){
      if(pinOnly && !m.pinned) return false;
      if(!q) return true;
      return m.text.toLowerCase().indexOf(q) !== -1 || m.id.toLowerCase().indexOf(q) !== -1;
    });
    items.sort(function(a,b){
      if(sort==='new') return (b.created||'').localeCompare(a.created||'');
      if(sort==='old') return (a.created||'').localeCompare(b.created||'');
      if(sort==='upd') return (b.updated||'').localeCompare(a.updated||'');
      if(sort==='used') return (b.count-a.count) || (b.created||'').localeCompare(a.created||'');
      if(sort==='pin') return (b.pinned-a.pinned) || (b.created||'').localeCompare(a.created||'');
      return 0;
    });

    grid.innerHTML='';
    items.forEach(function(m){
      var card = document.createElement('div');
      card.className = 'card' + (m.pinned ? ' pin' : '');

      var badges = document.createElement('div');
      badges.className = 'badges';
      if(m.pinned){ var b=document.createElement('span'); b.className='chip pin'; b.textContent='📌 코어'; badges.appendChild(b); }
      if(m.count>0){ var u=document.createElement('span'); u.className='chip'; u.textContent='조회 '+m.count+'회'; badges.appendChild(u); }
      card.appendChild(badges);

      var body = document.createElement('p');
      body.className = 'body clamp';
      body.innerHTML = highlight(m.text, q);
      card.appendChild(body);

      var more = document.createElement('button');
      more.className='more'; more.textContent='더보기'; more.hidden=true;
      more.addEventListener('click', function(){
        var on = body.classList.toggle('clamp');
        more.textContent = on ? '더보기' : '접기';
      });
      card.appendChild(more);

      var meta = document.createElement('div');
      meta.className='meta';
      var parts = ['생성 '+fmtDate(m.created)];
      if(m.updated && m.updated!==m.created) parts.push('수정 '+fmtDate(m.updated));
      if(m.last) parts.push('최근조회 '+fmtDay(m.last));
      meta.innerHTML = parts.map(function(t){return '<span>'+esc(t)+'</span>';}).join('') +
        '<span class="id">'+esc(m.id)+'<button data-id="'+esc(m.id)+'">복사</button></span>';
      card.appendChild(meta);

      grid.appendChild(card);
      // show "더보기" only when the body actually overflows the clamp
      if(body.scrollHeight > body.clientHeight + 4) more.hidden = false;
    });

    countEl.textContent = '표시 ' + items.length + ' / 전체 ' + DATA.total;
    emptyEl.hidden = items.length !== 0;
  }

  grid.addEventListener('click', function(e){
    var btn = e.target.closest('button[data-id]');
    if(!btn) return;
    navigator.clipboard && navigator.clipboard.writeText(btn.getAttribute('data-id'));
    var old = btn.textContent; btn.textContent='복사됨'; setTimeout(function(){ btn.textContent=old; }, 900);
  });

  var t;
  qEl.addEventListener('input', function(){ clearTimeout(t); t=setTimeout(render,80); });
  sortEl.addEventListener('change', render);
  pinEl.addEventListener('change', render);
  render();
})();
</script>
</body>
</html>
"""


def main():
    default_chroma = _expand(os.environ.get("MEM0_CHROMA_PATH", "~/.mem0-mcp/chroma"))
    state_dir = os.path.dirname(default_chroma)
    ap = argparse.ArgumentParser(description="Generate a read-only HTML card view of mem0 memories.")
    ap.add_argument("--out", default=os.path.join(state_dir, "memory-viewer.html"),
                    help="output HTML path (default: <store parent>/memory-viewer.html)")
    ap.add_argument("--chroma", default=default_chroma, help="Chroma persist dir")
    ap.add_argument("--collection", default=os.environ.get("MEM0_COLLECTION", "mem0"))
    ap.add_argument("--user", default=os.environ.get("MEM0_DEFAULT_USER", "developer_workspace"))
    ap.add_argument("--meta", default=os.environ.get("MEM0_META_FILE", os.path.join(state_dir, "memory_meta.json")))
    ap.add_argument("--open", action="store_true", help="open the file in the default browser when done")
    args = ap.parse_args()

    chroma_path = _expand(args.chroma)
    meta_path = _expand(args.meta)
    out_path = _expand(args.out)

    memories = load_memories(chroma_path, args.collection, args.user)
    pinned, access = load_meta(meta_path)
    payload = build_payload(memories, pinned, access, args.user)

    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_out = TEMPLATE.replace("__DATA_JSON__", data_json)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    print("✅ Wrote %d memories (%d pinned) -> %s"
          % (payload["total"], payload["pinnedCount"], out_path))
    if args.open:
        webbrowser.open("file://" + out_path)


if __name__ == "__main__":
    main()
