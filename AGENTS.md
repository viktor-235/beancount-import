# Repository rules

- Do not add `Co-Authored-By` lines or any other agent attribution to commit messages.
- When a change alters the UI, retake the affected screenshots in `docs/images` (always the main `review.png`) before committing. Build the frontend, run the demo (`python examples/jev/run.py --mock-jev`) and keep the same size and scale as the existing image (`review.png`: 1400×610 viewport at 2x, after clicking Classify with AI).

## Autonomous workflow

Propose it for non-trivial code tasks; do small edits directly. The main session is the orchestrator: it talks to the user, owns `PLAN.md`, picks models and runs subagents one at a time. Subagents get context only from `PLAN.md` and their prompt, never from chat history.

Models: latest Opus for planning, review, and implementation that spans several modules or has non-obvious logic; latest Sonnet for mechanical implementation of a detailed plan and small review fixes. Record the model in the Log.

### PLAN.md

Lives in the main working copy (gitignored); subagents read and write it by absolute path. Shared chapters at the top hold cross-task decisions. One chapter per user task:

```
## <Task N>: <title>   [status: grilling|planned|approved|in-progress|review|done]
### Spec   goal, decisions, acceptance criteria, out of scope (3–7 bullets each)
### Plan   - [ ] steps, risks
### Log    one line per phase run: phase, model, result, deviations
```

### Phases

1. **Spec** (orchestrator): clarify the task with `/grilling`, write Spec.
2. **Plan** (subagent): research the approach, best practices and risks; write a terse checklist into Plan. New questions → another `/grilling` round. Show the plan and wait for user approval before implementing.
3. **Implement** (subagent): once per task the orchestrator runs `git worktree add ~/worktrees/beancount-import-jev-2/<slug> -b task/<slug>` (outside iCloud; install deps there as needed) and passes its absolute path. Implement there, tick checkboxes, adjust the plan when needed, cover changes with tests (test-first at the implementer's discretion), follow the repository rules above, run tests, finish with `/simplify`. Do not commit.
4. **Review** (subagent, fresh context): `/code-review` on the worktree diff (`high` for Opus tasks, `medium` otherwise); check every acceptance criterion and checkbox. Findings → back to Plan (if the plan is wrong) or Implement, then Review again. Max 3 cycles; if correctness findings remain, stop and ask the user.
5. **Report** (orchestrator): what is done per acceptance criterion, deviations from the plan and why, remaining findings and open questions, test results, diff stat, proposed commit message. On user approval: commit in `task/<slug>`, merge into the working branch, remove the worktree and branch.
