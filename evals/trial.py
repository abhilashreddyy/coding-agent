"""One trial: build the task in an empty folder, let one version of the agent work on it, check the folder.
Run by evals/run.py inside a sandbox. Usage: trial.py <code_dir> <task> <workdir> <result.json>"""
import json, os, sys, time, traceback

code_dir, task_name, workdir, out = sys.argv[1:5]
sys.path.insert(0, code_dir)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tasks import TASKS

setup, prompt, check = TASKS[task_name]
state = setup(workdir)
result = {"task": task_name, "crash": None, "stopped": None, "steps": 0, "final_text": ""}
started = time.time()
meter = None
try:
    import agent, model
    out_run = agent.run_task(prompt, workdir)
    meter = out_run["meter"]
    result.update(stopped=out_run["stopped"], steps=out_run["steps"], final_text=out_run["final_text"][:3000], compactions=out_run.get("compactions", 0))
except BaseException as e:  # a crash is a result too
    result["crash"] = f"{type(e).__name__}: {str(e)[:300]}"
    result["trace"] = traceback.format_exc()[-1500:]
    meter = next((v for v in (getattr(e, "meter", None),) if v), None)
try:
    passed, extra = check(workdir, state, result["final_text"])
except Exception as e:
    passed, extra = False, {"check_error": str(e)[:200]}
result.update(passed=bool(passed), seconds=round(time.time() - started, 1), **extra)
if meter:
    result.update(calls=meter.calls, input_tokens=meter.input_tokens, output_tokens=meter.output_tokens, cost=round(meter.cost, 5), peak_context=meter.peak_context)
json.dump(result, open(out, "w"))
