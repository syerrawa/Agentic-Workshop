# Agentic AI Workshop

The VSF Club two-day workshop repo. On Saturday you spec, build and measure a support-ticket triage agent with your coding agent and BMad. On Sunday you do the same for a business case of your own choice.

## Start here

Open your coding agent (Claude Code, Codex, Copilot CLI or Cursor) in any folder and ask:

> Fork and clone github.com/lutic1/Agentic-Workshop, then install everything it needs.

Then reopen your agent inside the new `Agentic-Workshop` folder and ask:

> What are the rules for this repo?

If the answer ends with “— VSF”, your tool is reading `AGENTS.md` and you're ready.

You need `uv`, `git`, Node 20.12 or newer, a GitHub account and an OpenRouter API key. Copy `.env.example` to `.env` and paste your key there. The agent and the eval judge both run on OpenRouter; pick their models with `MODEL` and `JUDGE_MODEL`.

## Install BMad

BMad is a set of skills your agent loads: specs, builds, reviews and five personas. Everyone installs it for their own tool, with their own name, so the personas greet you. Ask your agent:

> Install BMad for Claude Code. My first name is Priya.

It runs this, with your tool's id and your name:

```
npx bmad-method@6.12.0 install --directory . --modules bmm --tools claude-code --user-name Priya --yes
```

| Your tool | Tool id | Skills land in | Call a skill |
|---|---|---|---|
| Claude Code | `claude-code` | `.claude/skills/` | `/bmad-help` |
| Codex | `codex` | `.agents/skills/` | `$bmad-help` |
| GitHub Copilot CLI | `github-copilot` | `.agents/skills/`, `.github/agents/` | “Use the bmad-help skill” |
| Cursor | `cursor` | `.agents/skills/` | “Use the bmad-help skill” |
| Gemini CLI | `gemini` | `.agents/skills/` | “Use the bmad-help skill” |

Then quit and reopen your agent, because skills load when it starts, and call bmad-help the way your tool does: `/bmad-help What should I run first?` in Claude Code. The install is git-ignored: it lives on your laptop, not in the repo, so switching to a stage branch never touches it.

## Skills that come with the repo

Eight best-practice skills from the teams behind the stack are committed in `.claude/skills/` (Claude Code) and `.agents/skills/` (every other tool). Your agent loads one when a task matches it; you don't call them yourself.

| Skill | From | Helps with |
|---|---|---|
| `langchain-fundamentals` | LangChain | `create_agent`, tools and the agent loop (Epic 2) |
| `langchain-middleware` | LangChain | The approval gate and structured output (Epic 2) |
| `mcp-builder` | Anthropic | MCP servers like `mcp/triage_server.py`, and Sunday's case server |
| `instrumenting-with-mlflow-tracing` | MLflow | Tracing the agent (Epics 2 and 3) |
| `retrieving-mlflow-traces` | MLflow | Finding traces by ID, status or tag |
| `analyzing-mlflow-trace` | MLflow | Working out why one run went wrong |
| `build-a-scorer` | MLflow | Picking code checks or an LLM judge for each criterion (Epic 3, Sunday) |
| `searching-mlflow-docs` | MLflow | Looking up current MLflow APIs |

They were added with `npx skills add <repo> --skill <name> --agent claude-code codex --copy`, and `skills-lock.json` records where each one came from; `npx skills update --project` refreshes them. Licenses are in `THIRD_PARTY_LICENSES`. For Sunday's take-home, `npx skills add vercel-labs/agent-skills --skill deploy-to-vercel` adds Vercel's deploy skill.

## What you build on Saturday

| Epic | What it adds | Spec |
|---|---|---|
| 1 | The triage decision schema, and a loader that puts `seed/` into SQLite | You write it from `INTENT.md` with `/bmad-spec` |
| 2 | A LangChain agent that calls `mcp/triage_server.py`, with a person approving escalations | `_bmad-output/specs/spec-epic-2/` |
| 3 | An MLflow eval over 20 labelled tickets: four code checks, one LLM judge and a token report | `_bmad-output/specs/spec-epic-3/` |

Every story goes through the same loop: build it with `/bmad-build`, review it with `/bmad-code-review` in a fresh chat, and merge only on a pass.

## Behind? Switch branch

Each block has a finished checkpoint. Ask your agent to switch you to it:

| Branch | Contains |
|---|---|
| `stage-1` | The Epic 1 spec and stories |
| `stage-2` | Epic 1 built: schema and loader |
| `stage-3` | Epic 2 built: the agent, its MCP tools and the approval gate |
| `stage-4` | Epic 3 built: the eval, the judge and the token report |

## What's here

- `AGENTS.md`: the rules every coding agent reads. `CLAUDE.md` and `GEMINI.md` point to it.
- `INTENT.md`: the intent for Epic 1.
- `TRIAGE_POLICY.md`: the categories, priorities and escalation rule the agent follows.
- `seed/`: the tickets and customers the agent triages.
- `eval/labelled_tickets.csv`: 20 tickets labelled by hand, including T-1099, which tries a prompt injection.
- `mcp/triage_server.py`: the MCP server with `get_ticket` and `get_customer_history`.
- `run_agent.py`: runs the agent on one ticket, with MLflow tracing on.
- `_bmad-output/specs/`: the specs and stories for each epic, written with BMad.
- `.claude/skills/`, `.agents/skills/`, `skills-lock.json`: the eight skills above. BMad's skills land beside them when you install it, git-ignored.
