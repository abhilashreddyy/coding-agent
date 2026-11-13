# A coding agent in 7 commits, measured at every one

## Try it: the chat page

```
uv venv && uv pip install anthropic        # once
echo "ANTHROPIC_API_KEY=sk-..." > .env     # once
.venv/bin/python chat.py                   # then open http://localhost:8765
```

Type a task and press Run. The page shows every step live: what the agent is about to do, each tool call
(find files, search, read, edit, write, run command) with its result, permission questions, and the final answer
with steps, cost and time. The agent works in `workspace/expense-tracker` (change the folder in the box).
The default task builds an expense tracker; start it afterwards with `python3 expense_tracker.py` in that folder.

From a terminal instead: `.venv/bin/python agent.py "your task"` in the project folder.

Each tag adds one idea to the same small agent. `evals/` scores every tag on the same tasks.

| Tag | Adds |
|---|---|
| `level-0-chat` | A chat loop. It cannot see files. |
| `level-1-loop` | The agent loop and one shell tool. |
| `level-2-tools` | Read, list, edit and write tools; long output is clipped. |
| `level-3-guardrails` | Errors go back to the model; step and cost limits; a system prompt. |
| `level-4-permissions` | Allow, deny or ask before every tool call. |
| `level-5-context` | Compacts a long history into a progress note and a notes file. |
| `level-6-subagents` | Helper agents with a fresh history that return only an answer. |
| `level-7-scoreboard` | The eval harness that scores every tag. |

## Looking at a run

Every trial is recorded under `runs/<date-time>/trials/<level>__<task>__<trial>/`: the full trace of model calls and tool results,
the project before and after, a diff, and the result. `runs/<date-time>/viewer.html` is a page for browsing all of it;
`python evals/replay.py <trial folder>` prints one trial in the terminal.

### In a standard trace viewer

The same recordings can be sent to any OpenTelemetry trace viewer. With Arize Phoenix running locally (`python -m phoenix.server.main serve`):
`python evals/export_otel.py runs/<date-time>` then open http://127.0.0.1:6006. Each level becomes a project; each trial is one trace.
