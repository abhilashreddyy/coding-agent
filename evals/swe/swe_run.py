"""Run our agent on SWE-rebench tasks (real bugs from real GitHub projects) and grade them the standard way.

  python3 evals/swe/swe_run.py --ids evals/swe/pilot10.json --tag level-6-subagents --name swe_pilot
  python3 evals/swe/swe_run.py --ids evals/swe/pilot10.json --gold        # check the pipeline: the real fix must pass
  python3 evals/swe/swe_run.py --ids evals/swe/pilot10.json --empty       # ... and no change must fail

Per task: container A is the task's own image; the agent works on /testbed there. Container B is a FRESH container of the
same image where the agent's patch is applied, the task's tests are put in place and run, and the task counts as resolved
only if every FAIL_TO_PASS and PASS_TO_PASS test passes (SWE-bench's rule). Output has the same layout as evals/run.py,
so viewer.py, usage.py and export_otel.py read it unchanged.
"""
import argparse, concurrent.futures, json, os, re, shlex, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
META_DIR = os.path.expanduser(os.environ.get("SWE_META", "~/projects/gemma4-dev-agent/data/swerebench/meta"))  # read-only
RT = os.path.join(REPO, "rt")  # agent runtime mounted into every container: python/, site/, code/<tag>/, tasks/
CONDA = "if [ -f /opt/miniconda3/bin/activate ]; then source /opt/miniconda3/bin/activate; conda activate testbed 2>/dev/null; fi"
GIT_ID = 'git config --global user.email agent@local && git config --global user.name agent && git config --global --add safe.directory "*"'
NON_TEST = (".json", ".png", "csv", ".txt", ".md", ".jpg", ".jpeg", ".pkl", ".yml", ".yaml", ".toml")
HIDE_FIX = r'''cd /testbed
git checkout -q --detach 2>/dev/null || true
git tag -l | xargs -r git tag -d >/dev/null
git for-each-ref --format='%(refname)' refs/heads refs/remotes refs/stash refs/notes | xargs -r -n1 git update-ref -d
git remote | xargs -r -n1 git remote remove
git reflog expire --expire=now --all
git gc --prune=now -q 2>/dev/null || true'''  # the images keep upstream history; without this `git log --all` shows the real fix

p = argparse.ArgumentParser()
p.add_argument("--ids", required=True)
p.add_argument("--tag", default="level-6-subagents")
p.add_argument("--name", default="swe")
p.add_argument("--trials", type=int, default=1)
p.add_argument("--model", default="claude-haiku-5-5")
p.add_argument("--workers", type=int, default=3)
p.add_argument("--max-steps", type=int, default=80)
p.add_argument("--task-budget", type=float, default=0.40, help="dollars one task may spend before the agent stops itself")
p.add_argument("--total-cap", type=float, default=3.0)
p.add_argument("--agent-timeout", type=int, default=1500)
p.add_argument("--gold", action="store_true")
p.add_argument("--empty", action="store_true")
p.add_argument("--regrade", help="a finished run folder: grade its saved patches again, without running the agent (no model cost)")
a = p.parse_args()
ids = json.load(open(a.ids))
ids = ids["ids"] if isinstance(ids, dict) else ids
level = "gold" if a.gold else "empty" if a.empty else "regrade" if a.regrade else a.tag
outdir = os.path.join(REPO, "runs", time.strftime("%Y%m%d-%H%M%S") + f"-{a.name}")
os.makedirs(os.path.join(outdir, "trials"))
json.dump({"args": vars(a), "started": os.path.basename(outdir), "benchmark": "nebius/SWE-rebench"}, open(os.path.join(outdir, "experiment.json"), "w"), indent=1)
shared = tempfile.mkdtemp(prefix="a7c-")  # ledger shared by all containers
os.chmod(shared, 0o777)


def text_only(patch):
    """Drop binary files from a patch. An agent's crashed experiment can leave a core dump in the folder; `git apply`
    rejects the whole patch because of it, and a correct fix is then graded as no change at all."""
    parts = re.split(r"(?m)^(?=diff --git )", patch)
    kept = [x for x in parts if "\nGIT binary patch\n" not in x and not re.search(r"(?m)^Binary files .* differ$", x)]
    return "".join(kept), len(parts) - len(kept)


def sh(args, timeout=600, **kw):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kw)


def dexec(cid, script, timeout=600, env=None):
    cmd = ["docker", "exec"] + sum((["-e", f"{k}={v}"] for k, v in (env or {}).items()), []) + [cid, "bash", "-lc", script]
    return sh(cmd, timeout=timeout)


def start(image, extra=()):
    r = sh(["docker", "run", "-d", "--rm", *extra, image, "sleep", "infinity"])
    if r.returncode:
        raise RuntimeError("docker run failed: " + r.stderr[-300:])
    return r.stdout.strip()


def put(cid, text, dest):
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
        f.write(text if text.endswith("\n") else text + "\n")
    sh(["docker", "cp", f.name, f"{cid}:{dest}"])
    os.unlink(f.name)


def venv_bin(meta):
    tc = (meta.get("install_config") or {}).get("test_cmd") or ""
    m = re.search(r"(?:^|[\s;&(])(?:\.\s+|source\s+)?(?:/testbed/)?([\w.\-/]+)/bin/(?:python[\d.]*|activate|pytest)\b", tc)
    if m and not m.group(1).startswith("/"):
        return "/testbed/" + m.group(1) + "/bin"
    return "/testbed/.venv/bin" if re.search(r"\buv\s+run\b", tc) else ""


def parse(log, v2):
    statuses, out = ("FAILED", "PASSED", "SKIPPED", "ERROR", "XFAIL"), {}
    tr = str.maketrans("", "", "".join(chr(c) for c in range(1, 32)))
    for line in log.split("\n"):
        if v2:
            line = re.sub(r"\[(\d+)m", "", line).translate(tr)
        if line.startswith(statuses):
            if line.startswith("FAILED"):
                line = line.replace(" - ", " ")
            parts = line.split()
            if len(parts) > 1:
                out[parts[1]] = parts[0]
        elif v2 and line.endswith(statuses):
            parts = line.split()
            if len(parts) >= 2:
                out[parts[0]] = parts[1]
    return out


def grade(meta, patch, keep):
    """Container B: the SWE-rebench grading steps."""
    ic, base = meta.get("install_config") or {}, meta["base_commit"]
    cid = start(meta["docker_image"])
    try:
        ex = lambda cmd, t=600: dexec(cid, f"{CONDA}; {cmd}", t)
        ex(GIT_ID)
        if patch.strip():
            put(cid, patch, "/tmp/agent.patch")
            r = ex("cd /testbed && (git apply -v /tmp/agent.patch || git apply -v --reject /tmp/agent.patch || patch --batch --fuzz=5 -p1 -i /tmp/agent.patch)")
            if r.returncode:
                return False, "patch did not apply: " + (r.stdout + r.stderr)[-400:], {}
        if ic.get("install"):
            ex(f"cd /testbed && {ic['install']} 2>&1", 900)
        tp = meta["test_patch"]
        touched = re.findall(r"^diff --git a/(.*) b/", tp, re.M)
        files = [f for f in dict.fromkeys(re.findall(r"^diff --git a/.* b/(.*)$", tp, re.M)) if not f.endswith(NON_TEST)]
        put(cid, tp, "/tmp/test.patch")
        q = " ".join(shlex.quote(t) for t in touched)
        r = ex(f'cd /testbed && for t in {q}; do git checkout {base} -- "$t" 2>/dev/null || rm -f "$t"; done; git apply -v /tmp/test.patch')
        if r.returncode:
            return False, "test patch did not apply: " + (r.stdout + r.stderr)[-400:], {}
        test_cmd = f"{ic.get('test_cmd') or 'pytest -rA'} {' '.join(shlex.quote(t) for t in files)}"
        r = ex(f"cd /testbed && {test_cmd} 2>&1", 1800)
        log = r.stdout + r.stderr
        open(os.path.join(keep, "test_output.txt"), "w").write(f"$ {test_cmd}\n{log[-30000:]}")
        status = parse(log, ic.get("log_parser") == "parse_log_pytest_v2")
        f2p, p2p = list(meta.get("FAIL_TO_PASS") or []), list(meta.get("PASS_TO_PASS") or [])
        bad_f = [t for t in f2p if status.get(t) not in ("PASSED", "XFAIL")]
        bad_p = [t for t in p2p if status.get(t) not in ("PASSED", "XFAIL")]
        detail = {"f2p_total": len(f2p), "f2p_passed": len(f2p) - len(bad_f), "p2p_total": len(p2p), "p2p_passed": len(p2p) - len(bad_p),
                  "f2p_failing": [f"{t} [{status.get(t, 'MISSING')}]" for t in bad_f[:5]], "p2p_failing": [f"{t} [{status.get(t, 'MISSING')}]" for t in bad_p[:5]]}
        return not bad_f and not bad_p, "", detail
    finally:
        sh(["docker", "kill", cid])


def one(job):
    iid, n = job
    meta = json.load(open(os.path.join(META_DIR, iid + ".json")))
    keep = os.path.join(outdir, "trials", f"{level}__{iid}__{n}")
    os.makedirs(keep)
    result = {"task": iid, "tag": level, "trial": n, "prompt": meta["problem_statement"][:6000], "repo": meta.get("repo"), "gold_patch_chars": len(meta["patch"]),
              "crash": None, "stopped": None, "steps": 0, "cost": 0.0}
    patch = meta["patch"] if a.gold else ""
    if a.regrade:
        import glob
        patch = open(glob.glob(os.path.join(a.regrade, "trials", f"*__{iid}__{n}", "changes.diff"))[0]).read()
    try:
        if not (a.gold or a.empty or a.regrade):
            work = os.path.join(shared, f"{iid}__{n}")
            os.makedirs(work)
            os.chmod(work, 0o777)
            cid = start(meta["docker_image"], ["-v", f"{RT}:/agent-rt:ro", "-v", f"{shared}:/a7c", "--env-file", os.path.join(REPO, ".env")])
            try:
                dexec(cid, f"{GIT_ID}\n{HIDE_FIX}", 300)
                env = {"TASK_ID": iid, "TASK_VENV_BIN": venv_bin(meta), "AGENT_MODEL": a.model, "AGENT_MAX_STEPS": a.max_steps, "AGENT_BUDGET_USD": a.task_budget,
                       "AGENT_RUN_CAP_USD": a.task_budget * 1.5, "AGENT_LEDGER": "/a7c/ledger", "AGENT_TOTAL_CAP_USD": a.total_cap, "AGENT_CONTEXT_LIMIT": 0, "PYTHONDONTWRITEBYTECODE": 1}
                try:
                    r = dexec(cid, f"cd /testbed && /agent-rt/python/bin/python3 /agent-rt/swe_trial.py {a.tag} /a7c/{iid}__{n}", a.agent_timeout, env)
                    open(os.path.join(keep, "stderr.txt"), "w").write(r.stderr[-20000:])
                except subprocess.TimeoutExpired:
                    result["crash"] = f"hung: stopped after {a.agent_timeout} s"
                ar = os.path.join(work, "agent_result.json")
                if os.path.exists(ar):
                    result.update(json.load(open(ar)))
                    result.update(task=iid, tag=level, trial=n, prompt=meta["problem_statement"][:6000])
                elif not result["crash"]:
                    result["crash"] = "agent produced no result (see stderr.txt)"
                if os.path.exists(os.path.join(work, "trace.jsonl")):
                    open(os.path.join(keep, "trace.jsonl"), "w").write(open(os.path.join(work, "trace.jsonl")).read())
                d = dexec(cid, "cd /testbed && git add -A -- . ':!.agent' ':!*.pyc' 2>/dev/null; git diff --cached HEAD", 120)
                patch = d.stdout
            finally:
                sh(["docker", "kill", cid])
        patch, result["binary_files_dropped"] = text_only(patch)
        open(os.path.join(keep, "changes.diff"), "w").write(patch)
        open(os.path.join(keep, "gold.diff"), "w").write(meta["patch"])
        result["patch_chars"] = len(patch)
        ok, err, detail = grade(meta, patch, keep)
        result.update(passed=bool(ok), grade_error=err, **detail)
    except Exception as e:
        result.update(passed=False, crash=(result.get("crash") or "") + f" runner error: {e}")
    json.dump(result, open(os.path.join(keep, "result.json"), "w"), indent=1)
    print(f"  {level:20} {iid:45} #{n} {'RESOLVED' if result.get('passed') else 'not resolved':12} steps={result.get('steps', 0):<3} ${result.get('cost', 0):.3f} "
          f"{result.get('seconds', 0):>5}s  {result.get('crash') or result.get('stopped') or ''}"[:190], flush=True)
    return result


jobs = [(i, n) for i in ids for n in range(a.trials)]
print(f"{len(jobs)} runs: {level}, model {a.model}, total cap ${a.total_cap}")
t0 = time.time()
with concurrent.futures.ThreadPoolExecutor(a.workers) as pool:
    results = list(pool.map(one, jobs))
open(os.path.join(outdir, "results.jsonl"), "w").write("\n".join(json.dumps(r) for r in results) + "\n")
done = sum(bool(r.get("passed")) for r in results)
print(f"\n{level}: resolved {done}/{len(results)}  total ${sum(r.get('cost', 0) for r in results):.3f}  {time.time() - t0:.0f} s\nSaved to {outdir}")
