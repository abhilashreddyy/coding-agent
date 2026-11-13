"""A chat page for the agent that shows every step live: what it thinks, each tool it calls, and what comes back.

  .venv/bin/python chat.py [project folder]      then open http://localhost:8765

Only the standard library. The agent works in the project folder (default: ./workspace/expense-tracker).
Commands that are not on the approved list show Allow / Deny buttons in the chat.
"""
import json, os, queue, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
for line in open(os.path.join(HERE, ".env")) if os.path.exists(os.path.join(HERE, ".env")) else []:
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        os.environ.setdefault(k, v)
# Building a whole app takes more steps and money than one bug fix.
for k, v in {"AGENT_MAX_STEPS": "80", "AGENT_BUDGET_USD": "0.5", "AGENT_RUN_CAP_USD": "0.75"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, HERE)
import agent, model  # noqa: E402

PORT = int(os.environ.get("PORT", "8765"))
FOLDER = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "workspace", "expense-tracker"))

events, lock = [], threading.Lock()  # every event of every run, in order; the page reads from an offset
answers = {}  # permission question id -> queue holding the user's answer
busy = threading.Event()


def emit(kind, **data):
    with lock:
        events.append({"kind": kind, "time": time.time(), **data})


def ask_in_chat(command):
    qid = f"q{time.time_ns()}"
    answers[qid] = queue.Queue()
    emit("ask", qid=qid, command=command)
    allowed = answers[qid].get()
    emit("answered", qid=qid, allowed=allowed)
    return allowed


agent.show = lambda kind, **data: emit(kind, **json.loads(json.dumps(data, default=str)))
agent.approve = ask_in_chat


def work(task, folder):
    os.makedirs(folder, exist_ok=True)
    began = time.time()
    emit("user", text=task, folder=folder)
    try:
        out = agent.run_task(task, folder)
        emit("done", text=out["final_text"] or f"Stopped: {out['stopped']}.", steps=out["steps"], cost=round(out["meter"].cost, 4),
             seconds=round(time.time() - began, 1))
    except Exception as e:
        emit("done", text=f"Error: {type(e).__name__}: {e}", steps=0, cost=0, seconds=round(time.time() - began, 1))
    finally:
        busy.clear()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, body, kind="application/json", code=200):
        body = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            return self.send(PAGE.replace("__FOLDER__", FOLDER).replace("__MODEL__", model.MODEL), "text/html; charset=utf-8")
        if self.path.startswith("/events"):  # server-sent events: everything from the offset on, then live
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            i = 0
            try:
                while True:
                    with lock:
                        new = events[i:]
                    for e in new:
                        self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
                    i += len(new)
                    self.wfile.flush()
                    time.sleep(0.15)
            except (BrokenPipeError, ConnectionResetError):
                return
        self.send("not found", "text/plain", 404)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/run":
            if busy.is_set():
                return self.send('{"error": "the agent is still working"}', code=409)
            busy.set()
            folder = os.path.abspath(os.path.expanduser(data.get("folder") or FOLDER))
            threading.Thread(target=work, args=(data["task"], folder), daemon=True).start()
            return self.send('{"ok": true}')
        if self.path == "/answer" and data.get("qid") in answers:
            answers.pop(data["qid"]).put(bool(data.get("allowed")))
            return self.send('{"ok": true}')
        self.send('{"error": "unknown"}', code=404)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Coding agent</title>
<style>
:root{--bg:#f7f5f0;--panel:#fff;--ink:#1f1e1c;--sub:#6f6b63;--line:#e6e1d6;--accent:#c96442;--tool:#f08a24;--ok:#1f9d63;--bad:#d64545;--user:#efeae0;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:2;background:rgba(247,245,240,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.bar{max-width:820px;margin:0 auto;padding:12px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.brand{font-weight:650;display:flex;gap:8px;align-items:center}.dot{width:10px;height:10px;border-radius:50%;background:var(--accent)}
.meta{color:var(--sub);font-size:13px;display:flex;gap:14px;margin-left:auto;font-variant-numeric:tabular-nums}
.meta b{color:var(--ink);font-weight:600}
main{max-width:820px;margin:0 auto;padding:24px 16px 180px}
.user{background:var(--user);border-radius:16px;padding:12px 16px;margin:18px 0 18px auto;max-width:85%;width:fit-content;white-space:pre-wrap}
.think{margin:14px 0 6px;color:var(--ink)}
.step{border:1px solid var(--line);background:var(--panel);border-radius:10px;margin:6px 0;overflow:hidden}
.step.helper{margin-left:28px;border-style:dashed}
.head{display:flex;gap:10px;align-items:center;padding:8px 12px;cursor:pointer;font-size:14px}
.ico{width:22px;height:22px;border-radius:6px;display:grid;place-items:center;font-size:12px;color:#fff;background:var(--tool);flex:none}
.label{font-weight:600}.arg{font-family:var(--mono);font-size:13px;color:var(--sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}
.state{font-size:12px;color:var(--sub);flex:none}.state.ok{color:var(--ok)}.state.bad{color:var(--bad)}
.spin{width:12px;height:12px;border:2px solid var(--line);border-top-color:var(--tool);border-radius:50%;animation:s .8s linear infinite;display:inline-block}
@keyframes s{to{transform:rotate(360deg)}}
.body{display:none;border-top:1px solid var(--line);background:#fbfaf7;padding:10px 12px;font:12.5px/1.5 var(--mono);white-space:pre-wrap;word-break:break-word;max-height:320px;overflow:auto}
.step.open .body{display:block}
.note{font-size:13px;color:var(--sub);margin:10px 0;display:flex;gap:8px;align-items:center}
.ask{border:1px solid #e9b949;background:#fff8e6;border-radius:10px;padding:12px;margin:8px 0}
.ask code{font-family:var(--mono);font-size:13px;background:#fff;padding:2px 6px;border-radius:4px;border:1px solid var(--line)}
.ask button{margin:10px 8px 0 0}
.final{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--ok);border-radius:10px;padding:14px 16px;margin:16px 0;white-space:pre-wrap}
.final code,.think code{font-family:var(--mono);font-size:.88em;background:#f1ede4;padding:1px 5px;border-radius:4px}
.final pre{font:13px/1.5 var(--mono);background:#1f1e1c;color:#f3efe6;padding:10px 12px;border-radius:8px;margin:6px 0;white-space:pre-wrap}
.stats{font-size:13px;color:var(--sub);margin-top:-8px}
footer{position:fixed;bottom:0;left:0;right:0;background:linear-gradient(transparent,var(--bg) 30%)}
.composer{max-width:820px;margin:0 auto;padding:24px 16px 16px}
.box{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:10px 12px;box-shadow:0 4px 20px rgba(0,0,0,.05)}
textarea{width:100%;border:0;outline:0;resize:none;font:inherit;background:transparent;color:var(--ink);min-height:48px}
.row{display:flex;gap:8px;align-items:center}
.row input{flex:1;min-width:0;border:1px solid var(--line);border-radius:8px;padding:6px 8px;font:13px var(--mono);color:var(--sub);background:#fbfaf7}
button{border:0;border-radius:10px;padding:8px 14px;font:600 14px inherit;cursor:pointer;background:var(--accent);color:#fff}
button.ghost{background:#eee9df;color:var(--ink)}button:disabled{opacity:.45;cursor:default}
.empty{color:var(--sub);text-align:center;margin-top:18vh}
.empty h1{font:500 30px Georgia,serif;color:var(--ink);margin:0 0 6px}
@media (max-width:600px){.meta{margin-left:0}.user{max-width:100%}}
</style></head><body>
<header><div class="bar"><div class="brand"><span class="dot"></span>Coding agent</div>
<div class="meta"><span>model <b>__MODEL__</b></span><span>steps <b id="steps">0</b></span><span>cost <b id="cost">$0.000</b></span><span>time <b id="time">0s</b></span></div></div></header>
<main id="log"><div class="empty" id="empty"><h1>What should I build?</h1>I work in the project folder below and show every step.</div></main>
<footer><div class="composer"><div class="box">
<textarea id="task" placeholder="Describe the task…">Build a small expense tracker as a web app in this folder. One Python file serves it with only the standard library and stores data in a JSON file. The page lets me add an expense (amount, category, note), lists all expenses, and shows the total per category. Add a few tests and run them. When everything works, start the app and give me the local URL to open it.</textarea>
<div class="row"><input id="folder" value="__FOLDER__" title="Project folder"><button id="go">Run</button></div></div></div></footer>
<script>
const $ = s => document.querySelector(s), log = $("#log");
const LABEL = {list_files:["Find files","▤"], search:["Search","⌕"], read_file:["Read","▸"], edit_file:["Edit","✎"], write_file:["Write","+"], shell:["Run command",">_"], spawn_agent:["Helper agent","◎"]};
const steps = {}; let started = 0, timer = null, running = false;
const esc = s => String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const md = s => esc(s).split(/```[a-z]*\n?/).map((part, i) => i % 2 ? `<pre>${part.replace(/\n$/, "")}</pre>` :
  part.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/^- /gm, "• ")).join("");
function add(html, cls){ const d = document.createElement("div"); d.className = cls; d.innerHTML = html; log.appendChild(d); scrollTo(0, document.body.scrollHeight); return d; }
function argOf(n, a){ return a.command ?? a.path ?? a.pattern ?? (a.prompt ? a.prompt.slice(0,90) : "") ?? ""; }
function meta(e){ if (e.steps != null) $("#steps").textContent = e.steps; if (e.cost != null) $("#cost").textContent = "$" + Number(e.cost).toFixed(3); }
function setRunning(on){ running = on; $("#go").disabled = on; if (on){ started = Date.now(); timer = setInterval(()=>$("#time").textContent = Math.round((Date.now()-started)/1000)+"s", 500);} else clearInterval(timer); }
function handle(e){
  $("#empty")?.remove(); meta(e);
  const h = e.helper ? " helper" : "";
  if (e.kind === "user"){ add(esc(e.text), "user"); setRunning(true); if (Date.now()/1000 - e.time < 5) $("#task").value = ""; }
  else if (e.kind === "think") add(md(e.text), "think" + h);
  else if (e.kind === "tool"){
    const [label, ico] = LABEL[e.name] || [e.name, "•"];
    const body = e.name === "write_file" ? (e.args.content || "") : e.name === "edit_file" ? "− " + (e.args.old_str||"") + "\n+ " + (e.args.new_str||"") : "";
    const d = add(`<div class="head"><span class="ico">${ico}</span><span class="label">${label}</span><span class="arg">${esc(argOf(e.name, e.args))}</span><span class="state"><span class="spin"></span></span></div><div class="body">${esc(body)}</div>`, "step" + h);
    d.querySelector(".head").onclick = () => d.classList.toggle("open"); steps[e.id] = d;
  }
  else if (e.kind === "result" && steps[e.id]){
    const d = steps[e.id], st = d.querySelector(".state"), b = d.querySelector(".body");
    st.className = "state " + (e.error ? "bad" : "ok"); st.textContent = e.error ? "error" : "done";
    b.textContent = (b.textContent ? b.textContent + "\n\n──── result ────\n" : "") + e.output;
    if (e.error) d.classList.add("open");
  }
  else if (e.kind === "helper_start") add(`◎ A helper agent starts with a fresh memory`, "note");
  else if (e.kind === "helper_end") add(`◎ The helper is done; only its answer comes back`, "note");
  else if (e.kind === "compact") add(`🗒 Memory was getting long, so the agent summarised it into a notes file and carried on`, "note");
  else if (e.kind === "stopped") add(`■ Stopped by a guardrail: ${esc(e.reason)}`, "note");
  else if (e.kind === "ask"){
    const d = add(`Permission needed to run <code>${esc(e.command)}</code><div><button data-a="1">Allow</button><button class="ghost" data-a="0">Deny</button></div>`, "ask");
    d.querySelectorAll("button").forEach(b => b.onclick = () => { d.querySelectorAll("button").forEach(x=>x.disabled=true);
      fetch("/answer", {method:"POST", body: JSON.stringify({qid: e.qid, allowed: b.dataset.a === "1"})}); });
  }
  else if (e.kind === "answered") add(e.allowed ? "✓ Allowed" : "✕ Denied", "note");
  else if (e.kind === "done"){ add(md(e.text), "final"); add(`${e.steps} steps · $${Number(e.cost).toFixed(3)} · ${e.seconds}s`, "stats"); $("#time").textContent = Math.round(e.seconds)+"s"; setRunning(false); }
}
new EventSource("/events").onmessage = m => handle(JSON.parse(m.data));
$("#go").onclick = () => { const task = $("#task").value.trim(); if (!task || running) return;
  fetch("/run", {method:"POST", body: JSON.stringify({task, folder: $("#folder").value})}); $("#task").value = ""; };
$("#task").addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#go").click(); });
</script></body></html>"""

if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Coding agent chat on http://localhost:{PORT}  (project folder: {FOLDER})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
