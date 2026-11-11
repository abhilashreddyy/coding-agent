"""The scoreboard. Runs every chosen tag of the agent on every task, several times, and prints one row per tag.

  python evals/run.py --tags level-1-loop level-3-guardrails --tasks fix_bug rename --trials 2
  python evals/run.py --fake            # scripted model, no API calls, to test the plumbing

Each trial runs in its own empty folder inside a sandbox that cannot write to your home folder,
cannot read key files, and can only open HTTPS connections.
"""
import argparse, concurrent.futures, json, os, shutil, signal, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from tasks import TASKS

ALL_TAGS = ["level-0-chat", "level-1-loop", "level-2-tools", "level-3-guardrails", "level-4-permissions", "level-5-context", "level-6-subagents"]
p = argparse.ArgumentParser()
p.add_argument("--tags", nargs="+", default=ALL_TAGS)
p.add_argument("--tasks", nargs="+", default=list(TASKS))
p.add_argument("--trials", type=int, default=2)
p.add_argument("--model", default="claude-haiku-5-5")
p.add_argument("--context-limit", type=int, default=20000, help="pretend context window, in tokens")
p.add_argument("--total-cap", type=float, default=3.0, help="stop the whole experiment at this many dollars")
p.add_argument("--run-cap", type=float, default=0.25, help="no single trial may cost more than this")
p.add_argument("--workers", type=int, default=4)
p.add_argument("--trial-timeout", type=int, default=300, help="seconds before a stuck trial is killed")
p.add_argument("--fake", action="store_true")
a = p.parse_args()

exp = tempfile.mkdtemp(prefix="agent-eval-")
exp = os.path.realpath(exp)
home = os.path.expanduser("~")
profile = os.path.join(exp, "jail.sb")
open(profile, "w").write(f'''(version 1)
(allow default)
(deny file-write* (subpath "/Users"))
(deny file-read* (regex #"/\\.env$") (subpath "{home}/.ssh") (subpath "{home}/.aws") (subpath "{home}/.claude") (subpath "{home}/Documents/Personal Projects"))
(deny network-outbound)
(allow network-outbound (remote tcp "*:443") (remote udp "*:53") (literal "/private/var/run/mDNSResponder"))
''')

env = dict(os.environ, AGENT_MODEL=a.model, AGENT_CONTEXT_LIMIT=str(a.context_limit), AGENT_ASK="deny", AGENT_LEDGER=os.path.join(exp, "ledger"),
           AGENT_TOTAL_CAP_USD=str(a.total_cap), AGENT_RUN_CAP_USD=str(a.run_cap), PYTHONDONTWRITEBYTECODE="1",
           PATH=os.path.dirname(sys.executable) + ":/usr/bin:/bin:/usr/sbin:/sbin")
if a.fake:
    env["AGENT_FAKE"] = "1"
else:  # the key goes in through the environment; the sandbox cannot read the .env file itself
    for line in open(os.path.join(REPO, ".env")):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            env[k] = v

for tag in a.tags:  # each tag's agent.py, with today's model.py
    d = os.path.join(exp, "code", tag)
    os.makedirs(d)
    open(os.path.join(d, "agent.py"), "w").write(subprocess.run(["git", "show", f"{tag}:agent.py"], cwd=REPO, capture_output=True, text=True, check=True).stdout)
    shutil.copy(os.path.join(REPO, "model.py"), d)
shutil.copytree(HERE, os.path.join(exp, "evals"))


def trial(job):
    tag, task, n = job
    work = os.path.join(exp, "work", f"{tag}__{task}__{n}")
    os.makedirs(work)
    out = work + ".json"
    keep = os.path.join(outdir, "trials", f"{tag}__{task}__{n}")  # everything about this trial is kept here
    os.makedirs(keep)
    tenv = dict(env, AGENT_TRACE=work + ".trace.jsonl")
    try:
        errfile = open(os.path.join(keep, "stderr.txt"), "w")
        proc = subprocess.Popen(["sandbox-exec", "-f", profile, sys.executable, os.path.join(exp, "evals", "trial.py"), os.path.join(exp, "code", tag), task, work, out],
                                env=tenv, cwd=work, stdout=subprocess.DEVNULL, stderr=errfile, start_new_session=True)
        try:
            proc.wait(timeout=a.trial_timeout)
            r = json.load(open(out)) if os.path.exists(out) else {"task": task, "passed": False, "crash": "no result (see stderr.txt)"}
        except subprocess.TimeoutExpired:
            r = {"task": task, "passed": False, "crash": f"hung: killed after {a.trial_timeout} s"}
        finally:  # kill the trial and anything it left running
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            errfile.close()
    except Exception as e:
        r = {"task": task, "passed": False, "crash": f"runner error: {e}"}
    r.update(tag=tag, trial=n, prompt=TASKS[task][1])
    if os.path.exists(work + ".trace.jsonl"):
        shutil.copy(work + ".trace.jsonl", os.path.join(keep, "trace.jsonl"))
    for suffix, name in ((".start", "start_folder"), (".end", "final_folder")):
        if os.path.exists(work + suffix):
            shutil.copytree(work + suffix, os.path.join(keep, name), ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    if os.path.exists(work + ".start") and os.path.exists(work + ".end"):  # what the agent changed, as a diff
        d = subprocess.run(["diff", "-ruN", "-x", "__pycache__", "-x", ".pytest_cache", "start_folder", "final_folder"], cwd=keep, capture_output=True, text=True, errors="replace")
        open(os.path.join(keep, "changes.diff"), "w").write(d.stdout)
    json.dump(r, open(os.path.join(keep, "result.json"), "w"), indent=1)
    print(f"  {tag:22} {task:11} #{n}  {'PASS' if r.get('passed') else 'fail'}  steps={r.get('steps', 0):<3} ${r.get('cost', 0):.4f}  {r.get('crash') or r.get('stopped') or ''}"[:150], flush=True)
    return r


stamp = time.strftime("%Y%m%d-%H%M%S")
outdir = os.path.join(REPO, "runs", stamp + ("-fake" if a.fake else ""))
os.makedirs(outdir)
json.dump({"args": vars(a), "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip(), "started": stamp},
          open(os.path.join(outdir, "experiment.json"), "w"), indent=1)
jobs = [(tag, task, n) for tag in a.tags for task in a.tasks for n in range(a.trials)]
print(f"{len(jobs)} trials, model {a.model}{' (FAKE)' if a.fake else ''}, cap ${a.total_cap}, pretend context window {a.context_limit} tokens")
started = time.time()
with concurrent.futures.ThreadPoolExecutor(a.workers) as pool:
    results = list(pool.map(trial, jobs))

open(os.path.join(outdir, "results.jsonl"), "w").write("\n".join(json.dumps(r) for r in results) + "\n")

rows = ["| Tag | Passed | Every-trial tasks | Crashed | Hit a limit | Planted command ran | Cheated or destroyed | Avg steps | Avg cost | Peak context |", "|---|---|---|---|---|---|---|---|---|---|"]
for tag in a.tags:
    rs = [r for r in results if r["tag"] == tag]
    by_task = {t: [r for r in rs if r["task"] == t] for t in a.tasks}
    solid = sum(all(r.get("passed") for r in v) for v in by_task.values())
    limits = sum(r.get("stopped") in ("context_overflow", "step_limit", "budget") for r in rs)
    rows.append(f"| {tag} | {sum(bool(r.get('passed')) for r in rs)}/{len(rs)} | {solid}/{len(a.tasks)} | {sum(bool(r.get('crash')) for r in rs)} | {limits} | "
                f"{sum(bool(r.get('injected')) for r in rs)} | {sum(bool(r.get('cheated') or r.get('destroyed')) for r in rs)} | {sum(r.get('steps', 0) for r in rs) / len(rs):.1f} | "
                f"${sum(r.get('cost', 0) for r in rs) / len(rs):.4f} | {max((r.get('peak_context', 0) for r in rs), default=0):,} |")
total = sum(r.get("cost", 0) for r in results)
table = "\n".join(rows) + f"\n\nTotal: ${total:.3f} for {len(results)} trials in {time.time() - started:.0f} s. Model {a.model}{' (FAKE)' if a.fake else ''}, {a.trials} trials per task, pretend context window {a.context_limit:,} tokens.\n"
open(os.path.join(outdir, "table.md"), "w").write(table)
print("\n" + table + f"\nSaved to {outdir}")
subprocess.run([sys.executable, os.path.join(HERE, "viewer.py"), outdir])
shutil.rmtree(exp, ignore_errors=True)
