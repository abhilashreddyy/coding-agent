"""The one place that talks to the model. Every level of the agent calls `ask()`.

It also keeps the money honest: every call's tokens and dollar cost are added to a
per-run counter, and to a shared ledger file so a whole experiment can have a hard cap.
Set AGENT_FAKE=1 to use a scripted fake model (no API calls, no cost) for dry runs.
"""
import json, os, time
from types import SimpleNamespace as NS

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-5-5")
# dollars per million tokens (input, output); Haiku 5.5 rate is for prompts up to 100K tokens
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-fable-5-2": (8.0, 40.0)}
RUN_CAP = float(os.environ.get("AGENT_RUN_CAP_USD", "0.25"))  # one task may never cost more than this
LEDGER = os.environ.get("AGENT_LEDGER")  # file shared by all runs of an experiment
TOTAL_CAP = float(os.environ.get("AGENT_TOTAL_CAP_USD", "3"))


class BudgetExceeded(Exception):
    pass


class Meter:
    """Tokens, dollars and model calls used by one run."""

    def __init__(self):
        self.calls = self.input_tokens = self.output_tokens = 0
        self.cost = 0.0
        self.peak_context = 0  # largest prompt sent in this run

    def add(self, usage):
        i, o = PRICES[MODEL]
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.peak_context = max(self.peak_context, usage.input_tokens)
        step = (usage.input_tokens * i + usage.output_tokens * o) / 1e6
        self.cost += step
        if LEDGER:  # several runs share this file, so lock it while we add to it
            import fcntl
            with open(LEDGER, "a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.seek(0)
                total = float(f.read() or 0) + step
                f.seek(0); f.truncate(); f.write(repr(total))
            if total > TOTAL_CAP:
                raise BudgetExceeded(f"experiment cap ${TOTAL_CAP} reached")
        if self.cost > RUN_CAP:
            raise BudgetExceeded(f"run cap ${RUN_CAP} reached")


_client = None


def ask(messages, meter, system=None, tools=None):
    """One request to the model. The API is stateless: we send the whole history every time."""
    if LEDGER and os.path.exists(LEDGER) and float(open(LEDGER).read() or 0) > TOTAL_CAP:
        raise BudgetExceeded(f"experiment cap ${TOTAL_CAP} reached")
    if os.environ.get("AGENT_FAKE"):
        response = _fake(messages, tools)
    else:
        global _client
        if _client is None:
            import anthropic
            ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
            _client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws} if ws else None)
        kwargs = dict(model=MODEL, max_tokens=8000, messages=messages)
        if system: kwargs["system"] = system
        if tools: kwargs["tools"] = tools
        # Thinking is switched off so that later levels may rewrite the history (see level 5).
        # Haiku 5.5 allows that; Opus 5.5 and Fable 5.2 do not, and there the API's own compaction is the route.
        if MODEL == "claude-haiku-5-5": kwargs["thinking"] = {"type": "disabled"}
        response = _client.messages.create(**kwargs)
    _trace(messages, system, tools, response)
    meter.add(response.usage)
    return response


# ---- a full recording of every run, for replaying later -------------------------------------
TRACE = os.environ.get("AGENT_TRACE")  # a .jsonl file; one line per model call
_seen = {}  # id(history list) -> how many of its messages are already written


def _plain(x):
    """Turn SDK objects into plain JSON."""
    if hasattr(x, "model_dump"): return x.model_dump()
    if hasattr(x, "__dict__") and not isinstance(x, type): return {k: _plain(v) for k, v in vars(x).items()}
    if isinstance(x, (list, tuple)): return [_plain(v) for v in x]
    if isinstance(x, dict): return {k: _plain(v) for k, v in x.items()}
    return x


def _trace(messages, system, tools, response):
    if not TRACE:
        return
    # A fresh history (a sub-agent, or a restart after compaction) is a new conversation: write all of it.
    key = id(messages)
    start = _seen.get(key, 0) if len(messages) >= _seen.get(key, 0) else 0
    line = {"time": time.time(), "conversation": key, "new_messages": _plain(messages[start:]), "history_length": len(messages),
            "system": system if start == 0 else None, "tool_names": [t["name"] for t in tools or []],
            "response": _plain(response.content), "stop_reason": response.stop_reason,
            "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    _seen[key] = len(messages) + 1  # the caller appends this response next
    with open(TRACE, "a") as f:
        f.write(json.dumps(line, default=str) + "\n")


def _fake(messages, tools):
    """A scripted stand-in: lists the folder once if it has a tool, then says it is done."""
    time.sleep(0.01)
    used_tool = any(isinstance(m["content"], list) and any(getattr(b, "type", None) == "tool_use" for b in m["content"]) for m in messages if m["role"] == "assistant")
    usage = NS(input_tokens=200 * len(messages), output_tokens=30)
    if tools and not used_tool:
        name = tools[0]["name"]
        args = {"command": "ls"} if "command" in tools[0]["input_schema"]["properties"] else {}
        return NS(stop_reason="tool_use", usage=usage, content=[NS(type="tool_use", id="fake_1", name=name, input=args)])
    return NS(stop_reason="end_turn", usage=usage, content=[NS(type="text", text="Done (fake model).")])
