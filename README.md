# Chosen Local Operating System

The AI systems that run Chosen Local, built with Claude Code (Opus) against the V1.1 business plan.

- `CLAUDE.md`: standing instructions Claude Code loads every session
- `.claude/settings.json`: blocks Claude Code from reading secrets and private prospect data
- `.claude/rules/`: extra rules that load only when Claude works in matching folders
- `docs/business-plan-v1.1.md`: the source of truth
- `docs/build-plan.md`: phases, specs, acceptance tests and a kickoff prompt for each phase
- `docs/decisions.md`: business inputs only the founders can supply, and their status
- `docs/inputs/`: where those inputs go
- `deploy/worker.env.example`: names of the production worker's environment variables

## Start

1. Finish the setup checklist (separate document).
2. From WSL: `cd ~/code/chosen-local-os && claude`
3. Paste the Phase 0 kickoff prompt from `docs/build-plan.md`.

Real recordings, transcripts and exports belong to the separate `clworker` Linux user and live in
`/home/clworker/cl-private/`, never in this repo. Claude Code cannot read them.
