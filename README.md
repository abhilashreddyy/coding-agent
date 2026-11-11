# A coding agent in 7 commits, measured at every one

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
