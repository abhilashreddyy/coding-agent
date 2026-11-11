"""Build a web page for looking at a recorded run: the scoreboard, and every trial step by step.

  python evals/viewer.py runs/<run>      -> writes runs/<run>/viewer.html (one file, opens offline)

Like the trace viewers used for agents in production: pick a trial, see each model call, tool call
and result in order, how the context grew, what it cost, and what changed in the project.
"""
import json, os, sys

run = sys.argv[1].rstrip("/")
LIMIT = 6000  # characters of any one block kept in the page; the full text stays in trace.jsonl


def cut(x):
    if isinstance(x, str):
        return x if len(x) <= LIMIT else x[:LIMIT] + f"\n… [{len(x) - LIMIT:,} more characters in trace.jsonl]"
    if isinstance(x, list): return [cut(v) for v in x]
    if isinstance(x, dict): return {k: (v if k == "full_length" else cut(v)) for k, v in x.items()}
    return x


def blocks(content):
    out = []
    for b in content if isinstance(content, list) else [content]:
        if isinstance(b, str): out.append({"type": "text", "text": b})
        elif b.get("type") == "tool_result":
            text = b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])
            out.append({"type": "tool_result", "text": text, "full_length": len(text), "is_error": bool(b.get("is_error")), "id": b.get("tool_use_id")})
        elif b.get("type") == "tool_use": out.append({"type": "tool_use", "name": b["name"], "input": b["input"], "id": b.get("id")})
        elif b.get("type") == "text": out.append({"type": "text", "text": b["text"]})
    return out


trials = []
for name in sorted(os.listdir(os.path.join(run, "trials"))):
    folder = os.path.join(run, "trials", name)
    if not os.path.exists(os.path.join(folder, "result.json")):
        continue
    result = json.load(open(os.path.join(folder, "result.json")))
    calls, convs = [], {}
    path = os.path.join(folder, "trace.jsonl")
    for line in (map(json.loads, open(path)) if os.path.exists(path) else []):
        conv = convs.setdefault(line["conversation"], len(convs) + 1)
        calls.append({"conv": conv, "time": line["time"], "seconds": line.get("model_seconds"), "input_tokens": line["input_tokens"], "output_tokens": line["output_tokens"],
                      "cost": line.get("cost", 0), "stop": line["stop_reason"], "history": line["history_length"], "fresh": line.get("system") is not None or len(calls) == 0,
                      "incoming": [{"role": m["role"], "blocks": blocks(m["content"])} for m in line["new_messages"] if m["role"] == "user"],
                      "reply": blocks(line["response"])})
    diff_path = os.path.join(folder, "changes.diff")
    result.pop("trace", None)
    trials.append(cut({"name": name, "result": result, "calls": calls, "diff": open(diff_path, errors="replace").read() if os.path.exists(diff_path) else ""}))

experiment = json.load(open(os.path.join(run, "experiment.json"))) if os.path.exists(os.path.join(run, "experiment.json")) else {}
usage_path = os.path.join(run, "usage.json")
usage = json.load(open(usage_path)) if os.path.exists(usage_path) else None
data = json.dumps({"experiment": experiment, "trials": trials, "usage": usage}).replace("</", "<\\/")

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent run viewer</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#16202b;--mute:#5d6b7a;--line:#dfe4ea;--pass:#1f9d55;--fail:#d64545;--warn:#c98a00;--tool:#2563eb;--model:#7c3aed;--code:#f0f3f6}
@media (prefers-color-scheme:dark){:root{--bg:#0e1319;--panel:#161d26;--ink:#e6ecf2;--mute:#93a1b0;--line:#27323f;--pass:#3fcf7f;--fail:#ff6b6b;--warn:#f0b429;--tool:#6ea8ff;--model:#b794f6;--code:#0b1016}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);background:var(--panel)}h1{font-size:18px;margin:0 0 2px}small,.mute{color:var(--mute)}
main{display:grid;grid-template-columns:minmax(360px,520px) 1fr;gap:16px;padding:16px 20px}@media(max-width:1000px){main{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;overflow:auto}h2{font-size:14px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute)}
table{border-collapse:collapse;width:100%}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{font-size:12px;color:var(--mute);font-weight:600}
.dot{display:inline-block;width:18px;height:18px;border-radius:5px;margin:1px 3px 1px 0;cursor:pointer;border:2px solid transparent;vertical-align:middle}
.dot.pass{background:var(--pass)}.dot.fail{background:var(--fail)}.dot.limit{background:var(--warn)}.dot.crash{background:#7b8794}.dot.sel{border-color:var(--ink)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 14px}.chip{background:var(--code);border-radius:8px;padding:6px 10px}.chip b{display:block;font-size:16px}
.badge{display:inline-block;padding:2px 9px;border-radius:99px;color:#fff;font-weight:700;font-size:12px}.badge.pass{background:var(--pass)}.badge.fail{background:var(--fail)}
svg{width:100%;height:150px;display:block}.bar{fill:var(--tool);cursor:pointer}.bar.now{fill:var(--warn)}.bar.out{fill:var(--model)}
.step{border-left:3px solid var(--line);margin:0 0 10px 6px;padding:2px 0 2px 12px}.step.hidden{display:none}.step.current{background:color-mix(in srgb,var(--warn) 12%,transparent)}
.call{font-size:12px;color:var(--mute);margin:14px 0 6px;border-top:1px dashed var(--line);padding-top:8px}.fresh{color:var(--warn);font-weight:700}
.who{font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.03em}.user .who{color:var(--mute)}.model .who{color:var(--model)}.tool_use .who{color:var(--tool)}.tool_result .who{color:var(--pass)}.tool_result.err .who{color:var(--fail)}
.step.user{border-color:var(--mute)}.step.model{border-color:var(--model)}.step.tool_use{border-color:var(--tool)}.step.tool_result{border-color:var(--pass)}.step.tool_result.err{border-color:var(--fail)}
pre{background:var(--code);border-radius:8px;padding:8px 10px;margin:4px 0 0;white-space:pre-wrap;word-break:break-word;font:12px/1.45 ui-monospace,Menlo,monospace;max-height:260px;overflow:auto}
.controls{display:flex;gap:8px;align-items:center;margin:6px 0 10px;flex-wrap:wrap}button,select{font:inherit;padding:5px 12px;border-radius:8px;border:1px solid var(--line);background:var(--code);color:var(--ink);cursor:pointer}
.add{color:var(--pass)}.del{color:var(--fail)}.hunk{color:var(--tool)}details summary{cursor:pointer;color:var(--mute);margin-top:10px}
.hud{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:8px;margin:4px 0 12px}.hud div{border:1px solid var(--line);border-radius:8px;padding:6px 10px}.hud b{display:block;font-size:20px;font-variant-numeric:tabular-nums}.hud .hit{border-color:var(--warn);background:color-mix(in srgb,var(--warn) 14%,transparent)}.hud small{color:var(--mute)}
.grp td{font-weight:700;color:var(--mute);font-size:12px;text-transform:uppercase}.zero{color:var(--mute)}
.legend span{margin-right:12px}.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px}
</style></head><body>
<header><h1>Agent run viewer</h1><small id="exp"></small></header>
<main><div><div class="panel"><h2>Scoreboard</h2><div id="board"></div><p class="legend mute"><span><i style="background:var(--pass)"></i>passed</span><span><i style="background:var(--fail)"></i>failed</span><span><i style="background:var(--warn)"></i>hit a limit</span><span><i style="background:#7b8794"></i>crashed</span><br>Click a square to open that trial.</p></div>
<div class="panel" style="margin-top:16px"><h2>Per level</h2><div id="levels"></div></div>
<div class="panel" style="margin-top:16px"><h2>How often each part fired (all trials)</h2><div id="usage"></div></div></div>
<div class="panel" id="detail"><p class="mute">Pick a trial on the left.</p></div></main>
<script>
const DATA = __DATA__;
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const T = DATA.trials, tags = [...new Set(T.map(t => t.result.tag))], tasks = [...new Set(T.map(t => t.result.task))];
const kind = r => r.crash ? 'crash' : r.passed ? 'pass' : ['context_overflow','step_limit','budget'].includes(r.stopped) ? 'limit' : 'fail';
const e = DATA.experiment.args || {};
document.getElementById('exp').textContent = `${DATA.experiment.started || ''} · model ${e.model || '?'}${e.fake ? ' (fake)' : ''} · ${T.length} trials · pretend context window ${(e.context_limit || 0).toLocaleString()} tokens · total $${T.reduce((a, t) => a + (t.result.cost || 0), 0).toFixed(3)}`;
let sel = null, shown = 1e9, timer = null;
function board() {
  let h = '<table><tr><th>Level</th>' + tasks.map(t => `<th>${esc(t)}</th>`).join('') + '</tr>';
  for (const tag of tags) { h += `<tr><td>${esc(tag)}</td>`; for (const task of tasks) { h += '<td>' + T.map((t, i) => t.result.tag === tag && t.result.task === task ? `<span class="dot ${kind(t.result)}${i === sel ? ' sel' : ''}" title="trial ${t.result.trial}" onclick="pick(${i})"></span>` : '').join('') + '</td>'; } h += '</tr>'; }
  document.getElementById('board').innerHTML = h + '</table>';
  let l = '<table><tr><th>Level</th><th>Passed</th><th>Avg steps</th><th>Avg cost</th><th>Peak context</th><th>Crashed</th><th>Planted cmd ran</th><th>Cheated</th></tr>';
  for (const tag of tags) { const r = T.filter(t => t.result.tag === tag).map(t => t.result), n = r.length, s = k => r.reduce((a, x) => a + (x[k] || 0), 0);
    l += `<tr><td>${esc(tag)}</td><td>${r.filter(x => x.passed).length}/${n}</td><td>${(s('steps') / n).toFixed(1)}</td><td>$${(s('cost') / n).toFixed(4)}</td><td>${Math.max(0, ...r.map(x => x.peak_context || 0)).toLocaleString()}</td><td>${r.filter(x => x.crash).length}</td><td>${r.filter(x => x.injected).length}</td><td>${r.filter(x => x.cheated).length}</td></tr>`; }
  document.getElementById('levels').innerHTML = l + '</table>';
  const U = DATA.usage; if (U) { let u = '<table><tr><th>What happened</th>' + U.levels.map(x => `<th>${esc(x.replace('level-', 'L').split('-')[0])}</th>`).join('') + '</tr>', g = '';
    for (const row of U.rows) { if (row.group !== g) { g = row.group; u += `<tr class="grp"><td colspan="${U.levels.length + 1}">${esc(g)}</td></tr>`; } u += `<tr><td>${esc(row.label)}</td>` + row.values.map(v => `<td class="${v ? '' : 'zero'}">${v.toLocaleString()}</td>`).join('') + '</tr>'; }
    document.getElementById('usage').innerHTML = u + '</table>'; }
}
// what each event means for the harness, so the counters can tick during playback
function flags(s) { const f = {}; if (s.head) { if (s.call > 0 && s.c.incoming.some(m => m.blocks.some(b => (b.text || '').includes('<progress_notes>')))) f.compactions = 1; return f; }
  const b = s.b, t = b.text || '';
  if (s.who === 'tool_use') { f.tools = 1; if (b.name === 'spawn_agent') f.helpers = 1; }
  if (s.who === 'tool_result') { if (t.includes('characters cut. Narrow the command')) f.clipped = 1; if (t.includes('made this exact call')) f.repeats = 1;
    if (t.startsWith('Blocked by policy') || t.startsWith('Not allowed:')) f.refused = 1; else if (t.includes('ran for more than 60 seconds')) f.timeouts = 1; else if (b.is_error) f.errors = 1; }
  return f; }
const HUD = [['tools', 'Tool calls'], ['clipped', 'Output cut short'], ['errors', 'Errors handed back'], ['timeouts', 'Commands killed'], ['repeats', 'Repeat warnings'], ['refused', 'Commands refused'], ['compactions', 'Compactions'], ['helpers', 'Helpers started']];
function steps(t) { const out = []; t.calls.forEach((c, n) => { out.push({call: n, head: true, c}); c.incoming.forEach(m => m.blocks.forEach(b => out.push({call: n, who: b.type === 'tool_result' ? 'tool_result' : 'user', b}))); c.reply.forEach(b => out.push({call: n, who: b.type === 'tool_use' ? 'tool_use' : 'model', b})); }); return out; }
function render() {
  const t = T[sel], r = t.result, S = steps(t), lim = e.context_limit || 0, max = Math.max(lim, ...t.calls.map(c => c.input_tokens), 1), W = 1000, bw = Math.max(4, Math.min(40, W / Math.max(1, t.calls.length) - 3));
  const cur = S[Math.min(shown, S.length) - 1], curCall = cur ? cur.call : -1;
  let svg = `<svg viewBox="0 0 ${W} 150" preserveAspectRatio="none">` + t.calls.map((c, n) => { const h = 120 * c.input_tokens / max; return `<rect class="bar${n === curCall ? ' now' : ''}" x="${n * (bw + 3)}" y="${130 - h}" width="${bw}" height="${h}" onclick="jump(${n})"><title>call ${n + 1}: ${c.input_tokens.toLocaleString()} tokens in</title></rect>`; }).join('') + (lim ? `<line x1="0" x2="${W}" y1="${130 - 120 * lim / max}" y2="${130 - 120 * lim / max}" stroke="var(--fail)" stroke-dasharray="6 4"/>` : '') + '</svg>';
  let h = `<h2>${esc(r.tag)} · ${esc(r.task)} · trial ${r.trial} <span class="badge ${r.passed ? 'pass' : 'fail'}">${r.passed ? 'PASS' : 'FAIL'}</span></h2>
  <p><b>Task:</b> ${esc(r.prompt || '')}</p>
  <div class="chips">${[['Stopped', r.crash ? 'crash' : r.stopped], ['Tool calls', r.steps], ['Model calls', r.calls ?? t.calls.length], ['Tokens in', (r.input_tokens || 0).toLocaleString()], ['Tokens out', (r.output_tokens || 0).toLocaleString()], ['Peak context', (r.peak_context || 0).toLocaleString()], ['Cost', '$' + (r.cost || 0).toFixed(4)], ['Time', (r.seconds || 0) + ' s'], ['Compactions', r.compactions || 0]].map(([k, v]) => `<div class="chip"><small>${k}</small><b>${esc(v ?? '–')}</b></div>`).join('')}</div>
  ${r.crash ? `<pre>${esc(r.crash)}</pre>` : ''}
  <h2>Context sent with each model call ${lim ? '<small>(dashed line: the pretend window)</small>' : ''}</h2>${svg}
  <h2>Harness activity so far</h2><div class="hud">${(() => { const tot = {}, now = cur ? flags(cur) : {}; S.slice(0, Math.min(shown, S.length)).forEach(x => { const f = flags(x); for (const k in f) tot[k] = (tot[k] || 0) + 1; });
    const spent = t.calls.slice(0, curCall + 1).reduce((a, c) => a + (c.cost || 0), 0), ctx = curCall >= 0 ? t.calls[curCall].input_tokens : 0;
    return HUD.map(([k, label]) => `<div class="${now[k] ? 'hit' : ''}"><small>${label}</small><b>${tot[k] || 0}</b></div>`).join('') + `<div><small>Context now</small><b>${ctx.toLocaleString()}</b></div><div><small>Spent so far</small><b>$${spent.toFixed(4)}</b></div>`; })()}</div>
  <div class="controls"><button onclick="play()">${timer ? 'Pause' : 'Play'}</button><button onclick="show(1)">Restart</button><button onclick="show(shown - 1)">Back</button><button onclick="show(Math.min(shown, ${S.length}) + 1)">Next</button><button onclick="show(1e9)">Show all</button>
  <select id="speed"><option value="1200">slow</option><option value="600" selected>normal</option><option value="200">fast</option></select><span class="mute">${Math.min(shown, S.length)} of ${S.length} events</span></div><div id="steps">`;
  S.forEach((s, i) => { const hid = i >= shown ? ' hidden' : '', now = i === Math.min(shown, S.length) - 1 && shown < 1e9 ? ' current' : '';
    if (s.head) { h += `<div class="call step${hid}" style="border:none;margin-left:0;padding-left:0" id="call${s.call}">Model call ${s.call + 1} · ${s.c.input_tokens.toLocaleString()} tokens in, ${s.c.output_tokens.toLocaleString()} out · $${(s.c.cost || 0).toFixed(5)} · ${s.c.seconds ?? '?'} s${s.c.fresh && s.call ? ' · <span class="fresh">new conversation (' + (s.c.conv > 1 ? 'conversation ' + s.c.conv : 'restart') + ')</span>' : ''}</div>`; return; }
    const b = s.b, body = b.type === 'tool_use' ? JSON.stringify(b.input, null, 1) : b.text, label = s.who === 'tool_use' ? 'model calls ' + b.name : s.who === 'tool_result' ? 'tool result' + (b.is_error ? ' (error)' : '') + (b.full_length ? ' · ' + b.full_length.toLocaleString() + ' characters' : '') : s.who;
    h += `<div class="step ${s.who}${b.is_error ? ' err' : ''}${hid}${now}"><div class="who">${esc(label)}</div><pre>${esc(body)}</pre></div>`; });
  h += '</div>' + (t.diff ? `<details open><summary>What the agent changed in the project (diff)</summary><pre style="max-height:420px">${t.diff.split('\n').map(x => `<span class="${x.startsWith('+') ? 'add' : x.startsWith('-') ? 'del' : x.startsWith('@@') ? 'hunk' : ''}">${esc(x)}</span>`).join('\n')}</pre></details>` : '<p class="mute">The agent changed no files.</p>') + (r.final_text ? `<details><summary>Final answer</summary><pre>${esc(r.final_text)}</pre></details>` : '');
  document.getElementById('detail').innerHTML = h; board();
  const c = document.querySelector('.step.current'); if (c) c.scrollIntoView({block: 'nearest'});
}
function pick(i) { sel = i; shown = 1e9; stop(); render(); }
function show(n) { shown = Math.max(1, n); render(); }
function jump(n) { const S = steps(T[sel]); shown = S.findIndex(s => s.head && s.call === n + 1); if (shown < 0) shown = 1e9; render(); const el = document.getElementById('call' + n); if (el) el.scrollIntoView(); }
function stop() { clearInterval(timer); timer = null; }
function play() { if (timer) { stop(); render(); return; } const n = steps(T[sel]).length; if (shown >= n) shown = 1; const ms = +document.getElementById('speed').value; timer = setInterval(() => { if (shown >= n) { stop(); render(); return; } shown++; const sp = document.getElementById('speed').value; render(); document.getElementById('speed').value = sp; }, ms); render(); }
board(); if (T.length) pick(0);
</script></body></html>"""
out = os.path.join(run, "viewer.html")
open(out, "w").write(PAGE.replace("__DATA__", data))
print("wrote", out, f"({os.path.getsize(out) / 1e6:.1f} MB, {len(trials)} trials)")
