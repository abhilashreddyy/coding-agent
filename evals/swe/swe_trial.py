"""Runs INSIDE a SWE-rebench task container: one version of our agent works on the repository at /testbed.

Started by swe_run.py. The agent's own Python and libraries live in /agent-rt (mounted read-only), separate from the
project's environment, so the agent's shell commands see exactly what the project's tests see.
"""
import json, os, sys, time, traceback

tag, out_dir = sys.argv[1], sys.argv[2]
sys.path[:0] = ["/agent-rt/site", f"/agent-rt/code/{tag}"]
os.makedirs(out_dir, exist_ok=True)
os.environ["AGENT_TRACE"] = os.path.join(out_dir, "trace.jsonl")
os.environ.setdefault("AGENT_ASK", "deny")
# the project's environment first on PATH for every command the agent runs (never the agent's own Python)
for candidate in filter(None, [os.environ.get("TASK_VENV_BIN"), "/opt/miniconda3/envs/testbed/bin"]):
    if os.path.isdir(candidate):
        os.environ["PATH"] = candidate + ":" + os.environ["PATH"]

meta = json.load(open("/agent-rt/tasks/" + os.environ["TASK_ID"] + ".json"))
prompt = ("You are working in a Python repository checked out in the current folder. Resolve the issue below by changing the project's source code. "
          "Reproduce the problem first if you can, make the smallest change that fixes it, and run the relevant existing tests before you finish. "
          "Do not change existing tests.\n\n<issue>\n" + meta["problem_statement"].strip() + "\n</issue>")

result = {"task": os.environ["TASK_ID"], "tag": tag, "crash": None, "stopped": None, "steps": 0, "final_text": "", "prompt": prompt}
started, meter = time.time(), None
try:
    import agent
    if hasattr(agent, "ALLOW"):  # the allow-list is configuration: these are the ordinary developer programs of a Python project
        agent.ALLOW |= {"pip", "pip3", "tox", "make", "uv", "conda", "bash", "sh", "env", "which", "nl", "file", "stat", "date", "ruff", "mypy", "black", "flake8", "nox", "hatch", "poetry", "pre-commit", "timeout", "export", "source", ".", "tree", "seq", "comm", "paste", "column"}
    run = agent.run_task(prompt, "/testbed")
    meter = run["meter"]
    result.update(stopped=run["stopped"], steps=run["steps"], final_text=run["final_text"][:4000], compactions=run.get("compactions", 0))
except BaseException as e:
    result["crash"] = f"{type(e).__name__}: {str(e)[:300]}"
    result["trace_back"] = traceback.format_exc()[-1500:]
result["seconds"] = round(time.time() - started, 1)
if meter is None:  # crashed: rebuild the bill from the recording
    try:
        calls = [json.loads(line) for line in open(os.environ["AGENT_TRACE"])]
        result.update(calls=len(calls), input_tokens=sum(c["input_tokens"] for c in calls), output_tokens=sum(c["output_tokens"] for c in calls),
                      cost=round(sum(c.get("cost", 0) for c in calls), 5), peak_context=max((c["input_tokens"] for c in calls), default=0))
    except Exception:
        pass
else:
    result.update(calls=meter.calls, input_tokens=meter.input_tokens, output_tokens=meter.output_tokens, cost=round(meter.cost, 5), peak_context=meter.peak_context)
json.dump(result, open(os.path.join(out_dir, "agent_result.json"), "w"), indent=1)
