# Evaluation Criteria — Per-Layer Checklists

## Prompt Layer

- [ ] Task description is unambiguous — a developer could act on it without asking questions
- [ ] Scope is defined — which files, modules, or areas are affected
- [ ] Type classification is accurate (feature vs bugfix vs refactor, etc.)
- [ ] Complexity level is reasonable and justified
- [ ] Ambiguities are explicitly listed (not hidden or assumed away)
- [ ] Assumptions are stated, not silently embedded

## Context Layer

- [ ] Relevant source files were actually read (not just guessed at)
- [ ] Directory structure was explored
- [ ] Git history was checked for recent changes in the affected area
- [ ] Dependencies (external APIs, modules, services) are identified
- [ ] Gaps are honestly noted — what couldn't be found or verified
- [ ] No premature judgment — context reports facts, not opinions

## Intent Layer

- [ ] Refined goal is more precise than the original prompt
- [ ] Success criteria are specific and verifiable
- [ ] At least one criterion is measurable (testable, observable)
- [ ] Constraints list what must NOT break
- [ ] Out-of-scope items are explicitly excluded
- [ ] Priority order resolves potential conflicts between goals

## Judgment Layer

- [ ] At least one risk is identified (even for "simple" tasks)
- [ ] At least one assumption is challenged
- [ ] Severity levels are calibrated (not all low, not all critical)
- [ ] Edge cases are specific to this task, not generic platitudes
- [ ] Missing considerations point to things other layers missed
- [ ] Go/no-go recommendation is justified

## Coherence Layer

- [ ] Final output directly addresses the task description
- [ ] Every risk from Judgment is addressed or explicitly acknowledged
- [ ] Success criteria from Intent are met or deviations explained
- [ ] Project conventions from CLAUDE.md are applied (if available)
- [ ] Skipped layer compensation is noted (if layers were skipped)
- [ ] Confidence level is honest — low confidence is fine if justified

## Cross-Cutting

- [ ] Layer boundaries respected — no layer does another layer's job
- [ ] Output is valid JSON matching the expected schema
- [ ] No conversational fluff ("Sure!", "Here's what I found", etc.)
- [ ] No questions asked — agents make best-effort judgments
