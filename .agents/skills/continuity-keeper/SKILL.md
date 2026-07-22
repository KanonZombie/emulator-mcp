---
name: continuity-keeper
description: Use this skill before and after coding tasks to preserve project continuity, detect conflicts with previous decisions, and update AI project memory files.
---

# Continuity Keeper Skill

Before changing code:

1. Read AGENTS.md.
2. Read docs/ai/PROJECT_STATE.md.
3. Read docs/ai/DONT_BREAK.md.
4. Read docs/ai/DECISIONS.md.
5. Summarize:
   - current architecture
   - relevant previous decisions
   - possible conflicts with the new request
   - files likely affected

During implementation:

- Prefer incremental changes.
- Do not delete existing behavior unless the user explicitly requested it.
- If replacing an old mechanism, preserve compatibility or document the migration.
- Mark temporary shortcuts as TEMPORARY.

After implementation:

1. Update docs/ai/PROJECT_STATE.md with the new state.
2. Add important decisions to docs/ai/DECISIONS.md.
3. Add any new "do not break" constraints to docs/ai/DONT_BREAK.md.
4. Add completed/pending work to docs/ai/CHANGELOG_AI.md.
5. Report what memory files were updated.
