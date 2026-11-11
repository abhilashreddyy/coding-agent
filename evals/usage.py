"""How often did each part of the harness actually do something? Counted from the recorded traces.

  python evals/usage.py runs/<run>        -> prints a table and writes runs/<run>/usage.md and usage.json

Nothing is re-run: every count comes from what the model called and what the harness answered.
"""
import collections, json, os, sys

run = sys.argv[1].rstrip("/")
ROWS = [  # (group, label, key)
    ("Tools", "Tool calls", "tool_calls"), ("Tools", "- shell", "tool:shell"), ("Tools", "- read_file", "tool:read_file"), ("Tools", "- list_files", "tool:list_files"),
    ("Tools", "- edit_file", "tool:edit_file"), ("Tools", "- write_file", "tool:write_file"), ("Tools", "Long output cut short", "clipped"), ("Tools", "Edit refused (text not found once)", "edit_refused"),
    ("Guardrails", "Tool error handed back to the model", "errors"), ("Guardrails", "Command killed after 60 s", "timeouts"), ("Guardrails", "Repeated-call warning", "repeats"),
    ("Guardrails", "Run stopped by step limit", "stop:step_limit"), ("Guardrails", "Run stopped by cost limit", "stop:budget"), ("Guardrails", "Run crashed or hung (no guardrail caught it)", "crashes"),
    ("Permissions", "Commands checked", "checked"), ("Permissions", "Refused: forbidden pattern", "denied_pattern"), ("Permissions", "Refused: program not approved", "denied_program"), ("Permissions", "Refused: outside the project", "denied_path"),
    ("Context", "History compacted", "compactions"), ("Context", "Run started from saved notes", "notes_loaded"), ("Context", "Run stopped: context full", "stop:context_overflow"),
    ("Sub-agents", "Helper agents started", "tool:spawn_agent"),
]
levels, counts, trials = [], collections.defaultdict(collections.Counter), collections.Counter()
for name in sorted(os.listdir(os.path.join(run, "trials"))):
    folder = os.path.join(run, "trials", name)
    if not os.path.exists(os.path.join(folder, "result.json")):
        continue
    result = json.load(open(os.path.join(folder, "result.json")))
    level = result["tag"]
    if level not in levels: levels.append(level)
    c = counts[level]
    trials[level] += 1
    c["crashes"] += bool(result.get("crash"))
    c["stop:" + str(result.get("stopped"))] += 1
    c["compactions"] += result.get("compactions", 0) or 0
    path = os.path.join(folder, "trace.jsonl")
    has_permissions = False
    for line in (map(json.loads, open(path)) if os.path.exists(path) else []):
        for m in line["new_messages"]:
            blocks = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
            for b in blocks:
                text = b.get("text") or (b.get("content") if isinstance(b.get("content"), str) else "") or ""
                if b.get("type") == "text" and "<progress_notes>" in text and line["history_length"] == 1 and c is not None:
                    c["notes_loaded"] += 1
                if b.get("type") != "tool_result":
                    continue
                if "characters cut. Narrow the command" in text: c["clipped"] += 1
                if text.startswith("Not edited:"): c["edit_refused"] += 1
                if "made this exact call" in text: c["repeats"] += 1
                if text.startswith("Blocked by policy"): c["denied_pattern"] += 1
                elif text.startswith("Not allowed:") and "outside the project" in text: c["denied_path"] += 1
                elif text.startswith("Not allowed:"): c["denied_program"] += 1
                elif "ran for more than 60 seconds" in text: c["timeouts"] += 1
                elif b.get("is_error"): c["errors"] += 1
        for b in line["response"]:
            if b.get("type") == "tool_use":
                c["tool_calls"] += 1
                c["tool:" + b["name"]] += 1
    c["notes_loaded"] -= result.get("compactions", 0) or 0  # a restart after compaction also begins from notes; count only fresh starts
    c["notes_loaded"] = max(0, c["notes_loaded"])
for level in levels:  # a level only "checks" commands if it has the permission layer
    n = int(level.split("-")[1])
    counts[level]["checked"] = counts[level]["tool_calls"] - counts[level]["tool:spawn_agent"] if n >= 4 else 0

short = [l.replace("level-", "L") for l in levels]
out = [f"Counts are totals over all trials at that level ({', '.join(f'{s}: {trials[l]}' for s, l in zip(short, levels))} trials). A dash means the level does not have that part.", "",
       "| Part | What happened | " + " | ".join(short) + " |", "|---|---|" + "---|" * len(levels)]
HAS = {"Tools": 1, "Guardrails": 3, "Permissions": 4, "Context": 5, "Sub-agents": 6}
for group, label, key in ROWS:
    cells = []
    for level in levels:
        n = int(level.split("-")[1])
        have = n >= HAS[group] or key in ("tool_calls", "tool:shell", "crashes", "stop:context_overflow") or (group == "Tools" and n >= 2)
        if key in ("clipped", "edit_refused", "tool:read_file", "tool:list_files", "tool:edit_file", "tool:write_file") and n < 2: have = False
        cells.append(f"{counts[level][key]:,}" if have else "–")
    out.append(f"| {group} | {label} | " + " | ".join(cells) + " |")
text = "\n".join(out) + "\n"
open(os.path.join(run, "usage.md"), "w").write(text)
json.dump({"levels": levels, "trials": trials, "rows": [{"group": g, "label": l, "values": [counts[lv][k] for lv in levels]} for g, l, k in ROWS]}, open(os.path.join(run, "usage.json"), "w"), indent=1)
print(text)
