"""Time the agent building a small app from one prompt, in an empty sandboxed folder.

  .venv/bin/python evals/build_app.py [tag]      -> prints time, steps, cost; keeps the app and the trace under runs/app-<time>/

Run by hand it uses the current agent.py; with a tag it uses that level's agent.
"""
import json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PROMPT = ("Build a small expense tracker as a web app in this folder. One Python file serves it with only the standard library and stores data in a JSON file. "
          "The page lets me add an expense (amount, category, note), lists all expenses, and shows the total per category. Add a few tests and run them. "
          "Tell me the command to start it.")
tag = sys.argv[1] if len(sys.argv) > 1 else None
work = os.path.realpath(tempfile.mkdtemp(prefix="agent-app-"))
code = os.path.join(work, "_code")
app = os.path.join(work, "app")
os.makedirs(code)
os.makedirs(app)
src = subprocess.run(["git", "show", f"{tag}:agent.py"], cwd=REPO, capture_output=True, text=True, check=True).stdout if tag else open(os.path.join(REPO, "agent.py")).read()
open(os.path.join(code, "agent.py"), "w").write(src)
shutil.copy(os.path.join(REPO, "model.py"), code)
home = os.path.expanduser("~")
profile = os.path.join(work, "jail.sb")
open(profile, "w").write(f'''(version 1)
(allow default)
(deny file-write* (subpath "/Users"))
(deny file-read* (regex #"/\\.env$") (subpath "{home}/.ssh") (subpath "{home}/.aws") (subpath "{home}/.claude") (subpath "{home}/Documents/Personal Projects"))
(deny network-outbound)
(allow network-outbound (remote tcp "*:443") (remote udp "*:53") (remote tcp "localhost:*") (literal "/private/var/run/mDNSResponder"))
''')
env = dict(os.environ, AGENT_ASK="deny", AGENT_TRACE=os.path.join(work, "trace.jsonl"), AGENT_MAX_STEPS="80", AGENT_BUDGET_USD="0.5", AGENT_RUN_CAP_USD="0.75",
           PYTHONDONTWRITEBYTECODE="1", PATH=os.path.dirname(sys.executable) + ":/usr/bin:/bin:/usr/sbin:/sbin")
for line in open(os.path.join(REPO, ".env")):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        env[k] = v
runner = f'''import json, sys, time
sys.path.insert(0, {code!r})
import agent
t = time.time()
out = agent.run_task({PROMPT!r}, {app!r})
m = out["meter"]
json.dump({{"seconds": round(time.time() - t, 1), "steps": out["steps"], "stopped": out["stopped"], "model_calls": m.calls, "input_tokens": m.input_tokens,
           "output_tokens": m.output_tokens, "cost": round(m.cost, 4), "compactions": out.get("compactions", 0), "final_text": out["final_text"]}}, open({os.path.join(work, "result.json")!r}, "w"), indent=1)
'''
proc = subprocess.Popen(["sandbox-exec", "-f", profile, sys.executable, "-c", runner], env=env, cwd=app, start_new_session=True)
try:
    proc.wait(timeout=900)
except subprocess.TimeoutExpired:
    print("stopped after 900 s")
finally:
    try: os.killpg(proc.pid, 9)
    except ProcessLookupError: pass
keep = os.path.join(REPO, "runs", "app-" + time.strftime("%Y%m%d-%H%M%S"))
shutil.copytree(work, keep, ignore=shutil.ignore_patterns("_code", "jail.sb", "__pycache__", ".pytest_cache"))
r = json.load(open(os.path.join(keep, "result.json"))) if os.path.exists(os.path.join(keep, "result.json")) else {}
print(json.dumps({k: v for k, v in r.items() if k != "final_text"}, indent=1))
print("\nFINAL ANSWER:\n" + r.get("final_text", "(none)"))
print("\nfiles:", sorted(os.listdir(os.path.join(keep, "app"))))
print("kept in", keep)
