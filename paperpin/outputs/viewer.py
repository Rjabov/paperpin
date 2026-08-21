"""Self-contained HTML viewer (§4.2 `paperpin view`) — dark lab aesthetic.

One file, zero external requests: page images inline as data URIs, result data
inline as JSON. Field list ↔ box hover sync, keyboard j/k, status colors
matching the product language (green verified / amber caution / red not_found
/ grey not_present).
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Optional

from PIL import Image

from ..types import GroundResult

_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>paperpin — __TITLE__</title>
<style>
:root{--bg:#0b1020;--panel:#111831;--panel2:#0e1428;--line:#232c4d;--text:#e8ecf8;
--muted:#8b96b8;--ok:#34d399;--warn:#fbbf24;--amb:#fb923c;--bad:#f87171;--np:#64748b;--accent:#60a5fa}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--text);font:14px/1.45 'Segoe UI',Roboto,Inter,sans-serif;display:flex;flex-direction:column}
header{padding:12px 18px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
header h1{font-size:16px;margin:0;font-weight:650}
header .sub{color:var(--muted);font-size:12.5px}
.legend{margin-left:auto;display:flex;gap:12px;font-size:12px;color:var(--muted)}
.dot{width:9px;height:9px;border-radius:3px;display:inline-block;margin-right:4px;vertical-align:-1px}
main{flex:1;display:flex;min-height:0}
#left{width:390px;min-width:300px;border-right:1px solid var(--line);overflow-y:auto;padding:10px}
.field{display:flex;gap:8px;padding:7px 9px;border-radius:8px;cursor:pointer;border:1px solid transparent;align-items:flex-start}
.field:hover{background:var(--panel)}.field.on{background:#182448;border-color:var(--accent)}
.field .st{width:9px;height:9px;border-radius:50%;margin-top:5px;flex:none}
.field .k{font-size:13px}.field .v{color:var(--muted);font-size:12.5px;word-break:break-word;font-family:Consolas,monospace}
.field .note{color:var(--muted);font-size:11px;margin-top:2px}
#right{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:14px;align-items:center}
.pagewrap{position:relative;box-shadow:0 8px 40px rgba(0,0,0,.55);flex:none}
.pagewrap img{display:block;max-width:100%;height:auto}
.box{position:absolute;border:2px solid;border-radius:2px;cursor:pointer}
.box.verified{border-color:var(--ok);background:rgba(52,211,153,.13)}
.box.low_confidence{border-color:var(--warn);background:rgba(251,191,36,.15)}
.box.ambiguous{border-color:var(--amb);background:rgba(251,146,60,.15);border-style:dashed}
.box.sel{box-shadow:0 0 0 3px rgba(96,165,250,.65),0 0 22px rgba(96,165,250,.5);z-index:5}
#banner{background:#7f1d1d;color:#fee2e2;padding:8px 18px;font-size:13px;display:none}
.summary{color:var(--muted);font-size:12.5px}
kbd{background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:11px}
</style></head><body>
<header><h1>paperpin</h1><span class="sub">__TITLE__</span>
<span class="summary" id="summary"></span>
<div class="legend">
<span><i class="dot" style="background:var(--ok)"></i>verified</span>
<span><i class="dot" style="background:var(--warn)"></i>low conf</span>
<span><i class="dot" style="background:var(--amb)"></i>ambiguous</span>
<span><i class="dot" style="background:var(--bad)"></i>not found</span>
<span><i class="dot" style="background:var(--np)"></i>not present</span>
<span><kbd>j</kbd>/<kbd>k</kbd> walk fields</span>
</div></header>
<div id="banner"></div>
<main><div id="left"></div><div id="right"></div></main>
<script>
const DATA = __DATA__;
const COLORS={verified:'var(--ok)',low_confidence:'var(--warn)',ambiguous:'var(--amb)',not_found:'var(--bad)',not_present:'var(--np)'};
const left=document.getElementById('left'),right=document.getElementById('right');
const fields=Object.entries(DATA.fields).map(([k,v])=>({key:k,...v}));
const summary=Object.entries(DATA.summary).map(([k,v])=>`${v} ${k}`).join(' · ');
document.getElementById('summary').textContent=summary;
const nf=fields.filter(f=>f.status==='not_found');
if(nf.length){const b=document.getElementById('banner');b.style.display='block';
b.textContent='⚠ HALLUCINATION FLAG — model asserted value(s) not on the document: '+nf.map(f=>`${f.key} = ${JSON.stringify(f.value)}`).join('  ·  ');}
DATA.pages.forEach((pg,i)=>{
  const wrap=document.createElement('div');wrap.className='pagewrap';wrap.dataset.page=i;
  const img=document.createElement('img');img.src=pg.src;wrap.appendChild(img);right.appendChild(wrap);
  fields.forEach((f,fi)=>{if(f.page!==i||!f.bbox)return;
    const bx=document.createElement('div');bx.className='box '+f.status;bx.dataset.fi=fi;
    bx.style.left=(f.bbox[0]*100)+'%';bx.style.top=(f.bbox[1]*100)+'%';
    bx.style.width=((f.bbox[2]-f.bbox[0])*100)+'%';bx.style.height=((f.bbox[3]-f.bbox[1])*100)+'%';
    bx.onclick=()=>select(fi,true);wrap.appendChild(bx);});
});
fields.forEach((f,fi)=>{const el=document.createElement('div');el.className='field';el.dataset.fi=fi;
  // extraction values are model output = untrusted; build with textContent only
  const st=document.createElement('span');st.className='st';st.style.background=COLORS[f.status]||'#888';
  const body=document.createElement('div');
  const k=document.createElement('div');k.className='k';k.textContent=f.key+' ';
  const badge=document.createElement('span');badge.style.color=COLORS[f.status];badge.style.fontSize='11px';
  badge.textContent=f.status;k.appendChild(badge);
  const val=document.createElement('div');val.className='v';
  val.textContent=f.value===null?'∅ null':String(f.value);
  body.appendChild(k);body.appendChild(val);
  if(f.evidence){const n=document.createElement('div');n.className='note';
    n.textContent='match: “'+f.evidence+'”'+(f.anchor?' · anchor: '+f.anchor:'');body.appendChild(n);}
  const notes=(f.notes||[]).join(' · ');
  if(notes){const n2=document.createElement('div');n2.className='note';n2.textContent=notes;body.appendChild(n2);}
  el.appendChild(st);el.appendChild(body);
  el.onclick=()=>select(fi,false);left.appendChild(el);});
let sel=-1;
function select(fi,fromBox){sel=fi;
  document.querySelectorAll('.field').forEach(e=>e.classList.toggle('on',+e.dataset.fi===fi));
  document.querySelectorAll('.box').forEach(e=>e.classList.toggle('sel',+e.dataset.fi===fi));
  const f=fields[fi];
  if(!fromBox&&f.page!==null&&f.bbox){const box=document.querySelector(`.box[data-fi="${fi}"]`);
    if(box)box.scrollIntoView({block:'center',behavior:'smooth'});}
  const el=document.querySelector(`.field[data-fi="${fi}"]`);
  if(el&&fromBox)el.scrollIntoView({block:'nearest',behavior:'smooth'});}
document.addEventListener('keydown',e=>{
  if(e.key==='j')select(Math.min(fields.length-1,sel+1),false);
  if(e.key==='k')select(Math.max(0,sel-1),false);});
</script></body></html>
"""


def render_viewer(result: GroundResult, out_path: str,
                  page_images: Optional[dict[int, Image.Image]] = None,
                  max_side: int = 1600) -> None:
    from .common import get_page_images
    images = page_images if page_images is not None else get_page_images(result)
    pages_payload = []
    for idx in sorted(images):
        img = images[idx].convert("RGB")
        if max(img.size) > max_side:
            img = img.copy()
            img.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        pages_payload.append({"src": f"data:image/jpeg;base64,{b64}",
                              "w": img.width, "h": img.height})

    payload = result.to_dict()
    payload.pop("meta", None)  # keep exports free of runtime objects/keys (E-37)
    payload["pages"] = pages_payload

    import html as _html
    title = _html.escape(Path(result.source).name or "document")
    # no raw '<' may reach the script block: '</script>' would close it and
    # '<!--' shifts the parser into script-data-escaped state (a code-bearing
    # document blanked the whole viewer). \u003c is invisible to JSON.
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    html = (_TEMPLATE
            .replace("__TITLE__", title)
            .replace("__DATA__", data))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
