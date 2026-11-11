"""Level 1: the agent loop. The model asks for a shell command, we run it, we send back
what it printed, and we repeat until the model stops asking. This loop never changes again."""
import os, subprocess, sys
from model import Meter, ask

# A pretend context window, so a short demo can hit the limit a real one hits after hours.
CONTEXT_LIMIT = int(os.environ.get("AGENT_CONTEXT_LIMIT", "0"))  # tokens; 0 = no limit

TOOLS = [{
    "name": "shell",
    "description": "Run a shell command in the project folder and return what it printed.",
    "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
}]


def shell(command, workdir):
    done = subprocess.run(command, shell=True, cwd=workdir, capture_output=True, text=True, timeout=60)
    return done.stdout + done.stderr


def run_tool(name, args, workdir):
    return shell(args["command"], workdir)


def run_task(prompt, workdir="."):
    meter, steps = Meter(), 0
    messages = [{"role": "user", "content": prompt}]
    while True:
        response = ask(messages, meter, tools=TOOLS)
        messages.append({"role": "assistant", "content": response.content})  # keep every block, unchanged
        if response.stop_reason != "tool_use":
            break
        if CONTEXT_LIMIT and response.usage.input_tokens > CONTEXT_LIMIT:
            return {"final_text": "", "steps": steps, "stopped": "context_overflow", "meter": meter}
        results = []
        for block in response.content:
            if block.type == "tool_use":
                steps += 1
                output = run_tool(block.name, block.input, workdir)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})  # all results together, in one message
    text = "".join(b.text for b in response.content if b.type == "text")
    return {"final_text": text, "steps": steps, "stopped": response.stop_reason, "meter": meter}


if __name__ == "__main__":
    out = run_task(" ".join(sys.argv[1:]) or input("task> "))
    print(out["final_text"])
    print(f"[{out['steps']} tool calls, ${out['meter'].cost:.4f}, stopped: {out['stopped']}]", file=sys.stderr)
