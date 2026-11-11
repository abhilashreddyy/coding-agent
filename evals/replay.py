"""Replay one recorded trial, step by step, exactly as it happened.

  python evals/replay.py runs/<run>/trials/<tag>__<task>__<n>            # print everything
  python evals/replay.py runs/<run>/trials/<tag>__<task>__<n> --live     # with the original pauses (capped at 3 s)
"""
import json, os, sys, time

folder = sys.argv[1]
live = "--live" in sys.argv
result = json.load(open(os.path.join(folder, "result.json")))
print(f"=== {result['tag']} / {result['task']} / trial {result['trial']}: {'PASS' if result.get('passed') else 'FAIL'}"
      f"  steps={result.get('steps')}  cost=${result.get('cost', 0):.4f}  stopped={result.get('crash') or result.get('stopped')}")


def show(block, who):
    kind = block.get("type") if isinstance(block, dict) else "text"
    if not isinstance(block, dict): print(f"\n[{who}] {block}")
    elif kind == "text": print(f"\n[{who}] {block['text']}")
    elif kind == "tool_use": print(f"\n[{who} calls {block['name']}] {json.dumps(block['input'])[:600]}")
    elif kind == "tool_result":
        text = block["content"] if isinstance(block["content"], str) else json.dumps(block["content"])
        print(f"\n[tool result{' ERROR' if block.get('is_error') else ''}, {len(text):,} characters]\n{text[:1200]}{' ...' if len(text) > 1200 else ''}")


path = os.path.join(folder, "trace.jsonl")
if not os.path.exists(path):
    sys.exit("No trace recorded for this trial.")
previous, conversations = None, {}
for n, line in enumerate(map(json.loads, open(path)), 1):
    if live and previous:
        time.sleep(min(3, max(0, line["time"] - previous)))
    previous = line["time"]
    label = conversations.setdefault(line["conversation"], len(conversations) + 1)
    print(f"\n----- model call {n} (conversation {label}; {line['input_tokens']:,} tokens in, {line['output_tokens']:,} out; history {line['history_length']} messages) -----")
    for m in line["new_messages"]:
        for block in (m["content"] if isinstance(m["content"], list) else [m["content"]]):
            if m["role"] == "user": show(block, "user")
    for block in line["response"]:
        show(block, "model")
    print(f"  (stop reason: {line['stop_reason']})")
print("\nFinal project files are in", os.path.join(folder, "final_folder"))
