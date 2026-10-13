---
paths: prompts/**, templates/**
---

# Prompt and template rules

- Prompt files are versioned: `prompts/<system>/<name>.vN.md`. Never edit a version in use; add a new
  one, run its evals, and switch the system's manifest (`PromptSpec`) to it only if scores hold or
  improve.
- Each prompt file starts with: purpose, inputs, output schema (the Pydantic model name), and the
  hard rules from CLAUDE.md that apply to it.
- Prompts that produce prospect- or coach-facing drafts must instruct the model to use only the facts
  supplied, cite vetting items by ID, and avoid any health, mental-health, addiction or medical advice.
- Prospect messages are written in plain, direct language as Chosen Local. A draft uses Sam's name
  only when the approval flow records Sam as the approver.
- Templates are written by the founders. You may fix placeholders and formatting, never the wording,
  unless Jack asks.
