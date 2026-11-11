"""Send a recorded run to a standard trace viewer over OpenTelemetry (the tracing standard used in production).

  .venv-phoenix/bin/python evals/export_otel.py runs/<run> [http://127.0.0.1:6006/v1/traces]

Nothing about the agent changes: this reads the trace.jsonl files the runs already wrote and replays them as spans,
using the OpenInference attribute names that Arize Phoenix (and other OpenTelemetry LLM viewers) understand.
One trace per trial: an AGENT span, with one LLM span per model call and one TOOL span per tool call.
"""
import json, os, sys
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

run = sys.argv[1].rstrip("/")
endpoint = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:6006/v1/traces"
ns = lambda seconds: int(seconds * 1e9)
text_of = lambda blocks: "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")


def export(project, trials):
    provider = TracerProvider(resource=Resource.create({"openinference.project.name": project, "service.name": "agent-in-7-commits"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    tracer = provider.get_tracer("agent-in-7-commits")
    for name in trials:
        folder = os.path.join(run, "trials", name)
        path = os.path.join(folder, "trace.jsonl")
        if not os.path.exists(path):
            continue
        result = json.load(open(os.path.join(folder, "result.json")))
        calls = [json.loads(line) for line in open(path)]
        if not calls:
            continue
        start = calls[0]["time"] - (calls[0].get("model_seconds") or 0)
        root = tracer.start_span(f"{result['task']} #{result['trial']}", start_time=ns(start), attributes={
            "openinference.span.kind": "AGENT", "input.value": result.get("prompt", ""), "output.value": result.get("final_text", "") or (result.get("crash") or ""),
            "agent.level": result["tag"], "eval.task": result["task"], "eval.trial": result["trial"], "eval.passed": bool(result.get("passed")),
            "eval.stopped": str(result.get("crash") or result.get("stopped")), "eval.steps": result.get("steps", 0), "eval.cost_usd": result.get("cost", 0.0),
            "tag.tags": [result["tag"], result["task"], "pass" if result.get("passed") else "fail"]})
        ctx = trace.set_span_in_context(root)
        for n, call in enumerate(calls):
            end = call["time"]
            begin = end - (call.get("model_seconds") or 0)
            attrs = {"openinference.span.kind": "LLM", "llm.model_name": call.get("model", ""), "llm.provider": "anthropic",
                     "llm.token_count.prompt": call["input_tokens"], "llm.token_count.completion": call["output_tokens"], "llm.token_count.total": call["input_tokens"] + call["output_tokens"],
                     "llm.cost_usd": call.get("cost", 0.0), "agent.history_length": call["history_length"], "agent.conversation": str(call["conversation"]), "llm.stop_reason": str(call["stop_reason"])}
            k = 0
            for m in call["new_messages"]:  # what was new in this request (the full history is the earlier spans)
                for b in (m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]):
                    body = b.get("text") if b.get("type") == "text" else (b.get("content") if isinstance(b.get("content"), str) else json.dumps(b.get("content", b.get("input", ""))))
                    attrs[f"llm.input_messages.{k}.message.role"] = "tool" if b.get("type") == "tool_result" else m["role"]
                    attrs[f"llm.input_messages.{k}.message.content"] = (body or "")[:20000]
                    k += 1
            attrs["input.value"] = attrs.get(f"llm.input_messages.{max(k - 1, 0)}.message.content", "")[:4000]
            attrs["llm.output_messages.0.message.role"] = "assistant"
            attrs["llm.output_messages.0.message.content"] = text_of(call["response"])
            uses = [b for b in call["response"] if b.get("type") == "tool_use"]
            for j, b in enumerate(uses):
                attrs[f"llm.output_messages.0.message.tool_calls.{j}.tool_call.function.name"] = b["name"]
                attrs[f"llm.output_messages.0.message.tool_calls.{j}.tool_call.function.arguments"] = json.dumps(b["input"])[:8000]
            attrs["output.value"] = text_of(call["response"]) or "; ".join(f"{b['name']}({json.dumps(b['input'])[:200]})" for b in uses)
            tracer.start_span(f"model call {n + 1}", context=ctx, start_time=ns(begin), attributes=attrs).end(end_time=ns(end))
            # tool calls ran between this model call and the next one; their results are in the next call's new messages
            nxt = calls[n + 1] if n + 1 < len(calls) else None
            if uses and nxt:
                results = {b.get("tool_use_id"): b for m in nxt["new_messages"] if isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result"}
                t0, t1 = end, max(end, nxt["time"] - (nxt.get("model_seconds") or 0))
                step = (t1 - t0) / len(uses)
                for j, b in enumerate(uses):
                    res = results.get(b.get("id"), {})
                    out = res.get("content", "") if isinstance(res.get("content", ""), str) else json.dumps(res.get("content"))
                    span = tracer.start_span(b["name"], context=ctx, start_time=ns(t0 + j * step), attributes={
                        "openinference.span.kind": "TOOL", "tool.name": b["name"], "tool.parameters": json.dumps(b["input"])[:8000],
                        "input.value": json.dumps(b["input"])[:8000], "output.value": out[:20000], "tool.output_characters": len(out)})
                    if res.get("is_error"):
                        span.set_status(Status(StatusCode.ERROR, out[:200]))
                    span.end(end_time=ns(t0 + (j + 1) * step))
        root.set_status(Status(StatusCode.OK if result.get("passed") else StatusCode.ERROR, "" if result.get("passed") else str(result.get("crash") or "check failed")))
        root.end(end_time=ns(calls[-1]["time"]))
    provider.shutdown()  # flush


names = sorted(os.listdir(os.path.join(run, "trials")))
levels = sorted({n.split("__")[0] for n in names})
for level in levels:  # one Phoenix project per level, so levels can be compared side by side
    mine = [n for n in names if n.startswith(level + "__")]
    export(f"{os.path.basename(run)} {level}", mine)
    print(f"sent {len(mine)} trials for {level}")
print("open http://127.0.0.1:6006")
