# AGENTS.md

## Purpose

This file defines the engineering principles that AI coding agents must follow when working on this repository.

The primary principle is:

> **YAGNI — You Aren't Gonna Need It.**

Implement only what is required by the current requirements, task, milestone, or explicitly approved design.

Do **not** build functionality merely because it might be useful in the future.

---

# 1. YAGNI Is the Default

When making implementation decisions, always prefer the **smallest complete solution that satisfies the current requirement**.

Do not implement:

* speculative future features
* unused abstractions
* unnecessary extension points
* hypothetical configuration options
* premature plugin systems
* premature generalization
* unused interfaces
* unused helper utilities
* unnecessary dependency injection
* extra API endpoints
* additional database fields for possible future use
* compatibility layers that are not currently required
* fallback mechanisms without an identified requirement
* abstractions intended only for potential future implementations

If a feature is not required now, **do not implement it now**.

---

# 2. Current Requirements Are the Source of Truth

Before implementing something, determine whether it is required by:

1. the current task,
2. the current milestone,
3. the project requirements,
4. an accepted architecture/design decision, or
5. an existing behavior that must be preserved.

If none of these require the functionality, assume it should **not** be implemented.

Do not turn:

> "We may want X later."

into:

> "I should build infrastructure for X now."

Instead, leave the system in a clean state where future changes can be added when they become actual requirements.

---

# 3. Do Not Overengineer

Prefer straightforward implementations over sophisticated architectures when both satisfy the requirement.

For example, prefer:

```text
Requirement
    ↓
Simple implementation
    ↓
Tests
```

over:

```text
Requirement
    ↓
Generic abstraction layer
    ↓
Factory
    ↓
Strategy registry
    ↓
Plugin system
    ↓
Configuration framework
    ↓
Implementation
```

unless those additional layers are explicitly justified by current requirements.

Complexity must solve a **real current problem**.

---

# 4. Avoid Premature Abstraction

Do not create abstractions solely because multiple implementations might exist someday.

Before introducing an abstraction such as:

* interface
* abstract base class
* adapter
* strategy
* factory
* registry
* plugin
* middleware layer
* generic framework

there should be a current reason for it.

Valid reasons include:

* multiple implementations already exist
* the requirements explicitly require interchangeable implementations
* testing requires a boundary
* an external system boundary needs isolation
* the architecture explicitly defines the abstraction

Invalid reason:

> "This could make it easier if we add another implementation later."

Future flexibility alone is not sufficient justification.

---

# 5. Do Not Build for Hypothetical Scale

Do not introduce complexity for traffic, data volume, concurrency, deployment scale, or distributed execution that the project does not currently require.

Examples of premature complexity include:

* distributed queues
* caching layers
* sharding
* microservices
* event buses
* complex concurrency
* distributed locks
* elaborate retry systems
* multi-region infrastructure

unless current requirements justify them.

Design cleanly, but optimize for the system that exists **now**.

---

# 6. Keep Configuration Minimal

Do not make every constant configurable.

Add configuration only when:

* users/operators actually need to change it,
* environments require different values,
* security requires external configuration, or
* requirements explicitly call for configurability.

Otherwise prefer a clear constant in the appropriate module.

Avoid creating configuration options "just in case."

---

# 7. Prefer Concrete Code Until Abstraction Is Earned

A small amount of duplication may be preferable to an incorrect abstraction.

Do not extract a shared abstraction after seeing something only once.

When similar code appears, first determine whether the concepts are genuinely the same.

Prefer:

> duplication → observe pattern → abstraction

over:

> prediction → abstraction → force code into abstraction

An abstraction should represent a stable concept, not merely similar-looking code.

---

# 8. Do Not Add Features While Refactoring

When asked to refactor, preserve behavior unless the task explicitly authorizes behavior changes.

Do not use a refactor as an opportunity to add:

* new features
* new options
* speculative validation
* unrelated architecture
* additional APIs
* unrelated cleanup across the repository

Keep the change focused.

---

# 9. Scope Discipline

Every implementation should remain within the scope of the current task.

Before changing a file, ask:

> Is this change necessary to satisfy the requested behavior?

If not, leave it alone unless fixing it is essential for the requested change to function correctly.

Avoid unrelated "while I'm here" changes.

Small, focused diffs are preferred.

---

# 10. Dependencies Must Be Justified

Do not add a third-party dependency when the requirement can reasonably be satisfied using:

* the standard library,
* an existing project dependency, or
* a small amount of straightforward code.

Every new dependency introduces:

* maintenance cost
* security risk
* compatibility risk
* upgrade burden
* additional project complexity

Add dependencies only when they provide meaningful current value.

---

# 11. Tests Should Follow YAGNI Too

Write enough tests to validate required behavior and important failure cases.

Do not create enormous test frameworks for hypothetical future behavior.

Prioritize tests for:

* acceptance criteria
* public behavior
* important edge cases
* regression-prone logic
* security/privacy boundaries
* known failure modes

Do not test imaginary future features.

---

# 12. Comments and TODOs

Do not add speculative TODOs such as:

```text
TODO: support Kubernetes someday
TODO: add additional providers
TODO: make this distributed
TODO: add plugin architecture
```

unless the future work is already documented as an approved requirement or issue.

Comments should primarily explain:

* why non-obvious code exists
* constraints
* invariants
* important tradeoffs

Do not use comments as a dumping ground for hypothetical features.

---

# 13. Future Requirements

YAGNI does **not** mean making future change unnecessarily difficult.

Code should still be:

* readable
* modular
* cohesive
* testable
* maintainable
* loosely coupled where appropriate

The goal is:

> **Simple today, changeable tomorrow.**

Not:

> **Build tomorrow's system today.**

When future functionality becomes an actual requirement, refactor the system at that time using the concrete information then available.

---

# 14. Relationship to Other Engineering Principles

Use YAGNI together with:

### KISS

Keep the solution as simple as reasonably possible.

### DRY

Remove meaningful duplication, but do not create premature abstractions simply to eliminate a few repeated lines.

### SOLID

Apply SOLID where it improves the current design. Do not create unnecessary classes, interfaces, or layers merely to appear SOLID-compliant.

### Separation of Concerns

Maintain clear responsibility boundaries, but do not split trivial functionality across unnecessary modules.

### Explicit Over Clever

Prefer understandable code over technically impressive code.

---

# 15. Decision Rule for Agents

When deciding whether to implement something, use this sequence:

```text
Is it explicitly required?
        │
        ├── Yes → Implement it.
        │
        └── No
             │
             ▼
Does existing required behavior depend on it?
        │
        ├── Yes → Implement only what is necessary.
        │
        └── No
             │
             ▼
Is it necessary to maintain correctness,
security, or a documented architecture constraint?
        │
        ├── Yes → Implement the minimum necessary.
        │
        └── No → DO NOT IMPLEMENT IT.
```

---

# 16. When Requirements Are Ambiguous

If implementation requires choosing between:

### A. A simple implementation satisfying the known requirement

and

### B. A more complex implementation supporting several possible future directions

choose **A**.

Do not infer future product requirements.

If an architectural choice would significantly constrain the project or introduce substantial complexity, document the tradeoff instead of silently implementing speculative infrastructure.

---

# 17. Before Writing Code

Before implementation, identify:

* the exact requirement being satisfied
* the minimum components that need modification
* the behavior that must remain unchanged
* the acceptance criteria
* whether any proposed abstraction is actually necessary

Remove speculative work from the implementation plan.

---

# 18. During Code Review

Flag code that appears to violate YAGNI.

Watch for phrases or reasoning such as:

* "we might need this later"
* "just in case"
* "for future extensibility"
* "eventually we could"
* "this gives us flexibility"
* "if another provider is added someday"

These statements are not sufficient justification by themselves.

Ask:

> What current requirement requires this complexity?

If there is no concrete answer, simplify or remove it.

---

# 19. Completion Criteria

A task is complete when:

* the requested behavior works
* acceptance criteria are satisfied
* relevant tests pass
* existing required behavior is preserved
* documentation is updated where necessary
* no unnecessary functionality was introduced

Do not continue adding enhancements after these conditions are satisfied.

---

# 20. Core Rule

When uncertain, default to:

> **Implement the requirement, not the imagined future.**

Build the smallest clean solution that solves the problem currently in front of the project.
