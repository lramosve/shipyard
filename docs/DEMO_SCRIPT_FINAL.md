# Shipyard — 5-Minute Demo Script

Target: Loom recording, ~5 minutes. Slides + live demo.

---

## [0:00–0:30] Slide 1 — Title + Intro

> "This is Shipyard — an autonomous coding agent I built from scratch with LangGraph, FastAPI, and GPT-4o. Its job: read a codebase, understand what needs to change, and make targeted edits without breaking everything around it. Then I pointed it at the Ship application and told it to rebuild it from scratch. Here's what happened."

**Action:** Show title slide, move to slide 2.

---

## [0:30–1:15] Slide 2 — What is Shipyard?

> "Shipyard runs as a persistent server — it accepts instructions via HTTP and loops: call the LLM, execute tools, call the LLM again, until the task is done. It never restarts between instructions. It has 11 tools — file reading, surgical editing, shell commands, web search, rollback. The key innovation is the editing strategy: anchor-based replacement. The LLM provides the exact text to find and the exact replacement. The system enforces that the match is unique — no ambiguous edits. If it gets it wrong, it gets an error with line numbers and self-corrects."

> "There's also a passive response filter — if the LLM says 'let me know' instead of taking action, the system escalates and forces it to use a tool. And a circuit breaker stops the agent after 5 consecutive complete failures."

**Action:** Walk through bullet points, gesture at the architecture diagram.

---

## [1:15–1:45] Slide 3 — Surgical File Editing

> "Here's the edit flow in detail. Read the file — mtime gets tracked. Call edit_file with old_string and new_string. Five guards run before the edit happens: was the file read? Is it stale? Is old_string unique? Zero matches gives a fuzzy hint. Multiple matches gives line numbers. One match — snapshot the old content, replace, write, update the tracker. This is the same strategy Claude Code and aider use. I have 11 unit tests covering every error path."

**Action:** Walk through the steps quickly. Move on.

---

## [1:45–2:45] Slide 4 — The Rebuild

> "The real test: I pointed Shipyard at the original Ship codebase — a 126,000-line TypeScript monorepo with Express, React, PostgreSQL, real-time collaboration, and the FleetGraph AI agent — and told it to rebuild it from scratch."

> "In about 24 hours and 18 commits, the agent produced a working application: FastAPI backend with 10 API routers, JWT authentication, 7 database tables, a React dashboard with Kanban boards and Gantt charts, analytics, WebSocket real-time updates, Docker packaging, and a CI/CD pipeline. 35 tests. 5,500 lines of code."

> "But it also missed things. It never attempted the FleetGraph agent — the most complex part. It skipped collaborative editing. It used SHA-256 for passwords instead of bcrypt. It swapped the language from TypeScript to Python without being asked. The 1,681-line monolithic App.tsx is... not how you'd structure a React app."

**Action:** Point to the green (built) and red (missed) columns.

---

## [2:45–3:30] Slide 5 — By the Numbers

> "Here's the quantitative comparison. 126,000 lines original versus 5,500 rebuilt — a 23-to-1 ratio. 39 database migrations versus 2. 73 E2E tests versus 5 smoke tests. The original took 6+ days with 85 commits in a 72-hour window alone. The rebuild took 24 hours."

> "That 23-to-1 ratio isn't just conciseness — it's feature omission. The original has a 44KB rich text editor, a 32KB collaboration server, a 60KB issue list. None of those exist in the rebuild."

**Action:** Walk through the table. Emphasize the LOC ratio and the dev speed.

---

## [3:30–4:00] Slides 6–7 — Key Findings + Decisions

> "The core insight: the agent optimizes for functional correctness — 'do tests pass?' — not architectural fidelity — 'does this match the original?' Non-functional requirements like security, accessibility, and extensibility are invisible to it unless you inject them explicitly."

> "On architecture decisions: LangGraph was the right call — saved 500 lines of loop management. Anchor-based editing works well under 300 lines. The language swap was wrong — that's a prompt design failure, not an agent limitation. The circuit breaker needed iteration — the first version was too aggressive."

**Action:** Quickly highlight green/yellow/red verdicts.

---

## [4:00–4:30] Slide 8 — If I Built It Again

> "Three changes that would matter most. First: an architecture extraction phase before writing any code — read the original, map the schema, catalog endpoints, produce a plan. This alone would have prevented the language swap and the FleetGraph omission. Second: specialized workers — an Architect, Coder, Tester, and Reviewer instead of just Coder and Researcher. The reviewer would have caught SHA-256 hashing. Third: inject the original codebase structure as persistent context so the agent always has a reference."

**Action:** Quick scan of the four quadrants.

---

## [4:30–5:00] Slide 9 — Live Demo + Close

> "The rebuilt Ship is deployed live on Railway."

**Action:** Switch to browser. Open https://frontend-production-20c1.up.railway.app. Log in with dev@ship.dev / admin123. Show the dashboard — click through Projects, Tasks, Analytics tabs. Show the Kanban board with seeded tasks.

> "All the code is on GitHub — the Shipyard agent at lramosve/shipyard, the rebuilt Ship at lramosve/new_ship, and the full comparative analysis is in the docs. Thanks for watching."

**Action:** Show the links slide. End recording.

---

## Tips for Recording

- **Screen layout:** Slides on one screen, browser ready on the other
- **Slide transitions:** Don't linger — the slides are dense, your voice carries the story
- **Live demo:** Keep it to 20–30 seconds max. Login, click 2–3 tabs, done.
- **Tone:** Confident and honest. The shortcomings section is the most important — own it.
- **Timing:** Practice once. If you're over 5 minutes, cut the Slide 3 (editing details) walkthrough shorter.
