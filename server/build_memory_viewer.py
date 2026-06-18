#!/usr/bin/env python3
"""
build_memory_viewer.py — generate a self-contained, READ-ONLY HTML card view of
your only-my-mem0ry memories.

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
    """Return (pinned:set, access:dict{id:{count,last}}, tags:dict{id:[...]},
    types:dict{id:"fact"}, provenance:dict{id:{origin,source}},
    confidence:dict{id:"high"}) from the sidecar."""
    meta = _load_sidecar(meta_path)
    return (set(meta.get("pinned") or []),
            (meta.get("access") or {}),
            (meta.get("tags") or {}),
            (meta.get("types") or {}),
            (meta.get("provenance") or {}),
            (meta.get("confidence") or {}))


# Order for the confidence filter dropdown (low < medium < high).
_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}


def build_payload(memories: list, pinned: set, access: dict, tags: dict,
                  types: dict, provenance: dict, confidence: dict, user: str) -> dict:
    records = []
    all_tags = set()
    all_types = set()
    all_origins = set()
    all_confs = set()
    for r in memories:
        st = access.get(r["id"]) or {}
        tg = tags.get(r["id"]) or []
        ty = types.get(r["id"]) or ""
        pv = provenance.get(r["id"]) or {}
        origin = pv.get("origin", "") or ""
        src = pv.get("source", "") or ""
        cf = confidence.get(r["id"]) or ""
        all_tags.update(tg)
        if ty:
            all_types.add(ty)
        if origin:
            all_origins.add(origin)
        if cf:
            all_confs.add(cf)
        records.append({
            "id": r["id"],
            "text": r["text"],
            "created": r["created"],
            "updated": r["updated"],
            "pinned": r["id"] in pinned,
            "count": int(st.get("count", 0) or 0),
            "last": st.get("last"),
            "tags": tg,
            "type": ty,
            "origin": origin,
            "source": src,
            "confidence": cf,
        })
    # Default order: newest first (the viewer re-sorts client-side anyway).
    records.sort(key=lambda x: (x["created"] or ""), reverse=True)
    return {
        "generated": datetime.now().astimezone().isoformat(),
        "user": user,
        "total": len(records),
        "pinnedCount": sum(1 for r in records if r["pinned"]),
        "allTags": sorted(all_tags),
        "allTypes": sorted(all_types),
        "allOrigins": sorted(all_origins),
        "allConfidences": sorted(all_confs, key=lambda c: _CONF_ORDER.get(c, 99)),
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
    --accent:#3b6ef5; --pin:#e0a400; --chip:#eef1f6; --mark:#fff2a8; --type:#7a3fd0; --prov:#0b8f6a; --conf:#c2410c;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#0e1116; --panel:#171b22; --ink:#e6e9ef; --muted:#9aa3b2; --line:#262c36;
      --accent:#6c93ff; --pin:#f0c548; --chip:#222834; --mark:#5e5320; --type:#b794f6; --prov:#34d399; --conf:#fb923c;
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
  .chip.tag{color:var(--accent);background:rgba(59,110,245,.13);cursor:pointer}
  .chip.type{color:var(--type);background:rgba(124,77,255,.14);cursor:pointer;text-transform:uppercase;letter-spacing:.4px}
  .chip.prov{color:var(--prov);background:rgba(16,185,129,.14);cursor:pointer}
  .chip.conf{color:var(--conf);background:rgba(194,65,12,.16);cursor:pointer;text-transform:uppercase;letter-spacing:.3px}
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
    <select id="typesel"><option value="">🗂️ 전체 유형</option></select>
    <select id="originsel"><option value="">🧭 전체 출처</option></select>
    <select id="confsel"><option value="">🎚️ 전체 신뢰도</option></select>
    <select id="tagsel"><option value="">🏷️ 전체 태그</option></select>
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
  var tagEl = document.getElementById('tagsel');
  var typeEl = document.getElementById('typesel');
  var originEl = document.getElementById('originsel');
  var confEl = document.getElementById('confsel');
  (DATA.allTags || []).forEach(function(t){
    var o = document.createElement('option'); o.value = t; o.textContent = '🏷️ ' + t;
    tagEl.appendChild(o);
  });
  (DATA.allTypes || []).forEach(function(t){
    var o = document.createElement('option'); o.value = t; o.textContent = '🗂️ ' + t;
    typeEl.appendChild(o);
  });
  (DATA.allOrigins || []).forEach(function(t){
    var o = document.createElement('option'); o.value = t; o.textContent = '🧭 ' + t;
    originEl.appendChild(o);
  });
  (DATA.allConfidences || []).forEach(function(t){
    var o = document.createElement('option'); o.value = t; o.textContent = '🎚️ ' + t;
    confEl.appendChild(o);
  });

  document.getElementById('foot').textContent =
    '전체 ' + DATA.total + '개 · 핀 ' + DATA.pinnedCount + '개 · 유형 ' + (DATA.allTypes||[]).length +
    '종 · 출처 ' + (DATA.allOrigins||[]).length + '종 · 신뢰도 ' + (DATA.allConfidences||[]).length +
    '종 · 태그 ' + (DATA.allTags||[]).length +
    '종 · 사용자 ' + DATA.user + ' · 생성 ' + fmtDate(DATA.generated);

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
    var tagSel = tagEl.value;
    var typeSel = typeEl.value;
    var originSel = originEl.value;
    var confSel = confEl.value;
    var items = DATA.memories.filter(function(m){
      if(pinOnly && !m.pinned) return false;
      if(typeSel && m.type !== typeSel) return false;
      if(originSel && m.origin !== originSel) return false;
      if(confSel && m.confidence !== confSel) return false;
      if(tagSel && (m.tags || []).indexOf(tagSel) === -1) return false;
      if(!q) return true;
      return m.text.toLowerCase().indexOf(q) !== -1 ||
             m.id.toLowerCase().indexOf(q) !== -1 ||
             (m.type || '').toLowerCase().indexOf(q) !== -1 ||
             (m.origin || '').toLowerCase().indexOf(q) !== -1 ||
             (m.source || '').toLowerCase().indexOf(q) !== -1 ||
             (m.confidence || '').toLowerCase().indexOf(q) !== -1 ||
             (m.tags || []).join(' ').toLowerCase().indexOf(q) !== -1;
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
      if(m.type){ var ty=document.createElement('span'); ty.className='chip type'; ty.textContent=m.type;
        ty.title='이 유형으로 필터'; ty.addEventListener('click', function(){ typeEl.value=m.type; render(); }); badges.appendChild(ty); }
      if(m.origin || m.source){ var pv=document.createElement('span'); pv.className='chip prov';
        pv.textContent='🧭 '+(m.origin||'')+(m.source ? (m.origin?' · ':'')+m.source : '');
        if(m.origin){ pv.title='이 출처로 필터'; pv.addEventListener('click', function(){ originEl.value=m.origin; render(); }); }
        badges.appendChild(pv); }
      if(m.confidence){ var cf=document.createElement('span'); cf.className='chip conf';
        cf.textContent='🎚️ '+m.confidence; cf.title='이 신뢰도로 필터';
        cf.addEventListener('click', function(){ confEl.value=m.confidence; render(); }); badges.appendChild(cf); }
      if(m.count>0){ var u=document.createElement('span'); u.className='chip'; u.textContent='조회 '+m.count+'회'; badges.appendChild(u); }
      (m.tags || []).forEach(function(t){
        var c=document.createElement('span'); c.className='chip tag'; c.textContent='#'+t;
        c.title='이 태그로 필터'; c.addEventListener('click', function(){ tagEl.value=t; render(); });
        badges.appendChild(c);
      });
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
  tagEl.addEventListener('change', render);
  typeEl.addEventListener('change', render);
  originEl.addEventListener('change', render);
  confEl.addEventListener('change', render);
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
    pinned, access, tags, types, provenance, confidence = load_meta(meta_path)
    payload = build_payload(memories, pinned, access, tags, types, provenance, confidence, args.user)

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
