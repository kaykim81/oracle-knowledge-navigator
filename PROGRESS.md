# Build Progress

Tracks build state phase by phase. See `MASTER_PLAN.md` for the plan, `CLAUDE.md` for the standing rules.

---

## Phase 0: VPS Preparation — ✅ COMPLETE (2026-05-29)

**What was done:**

- Steps 1–4 (env, git init, `.gitignore`): done prior to this session.
- **Step 5** — `.env.example` authored in the dev container with all six placeholders (`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `TRAEFIK_NETWORK`, `PUBLIC_HOSTNAME`, `BASIC_AUTH_USER`, `BASIC_AUTH_PASSWORD_HASH`), including the single-`$` htpasswd caveat in comments.
- **Step 6** — DNS A record `navigator.p36server.com → 89.116.167.171` created at the provider. Verified resolving at public resolvers 8.8.8.8 and 1.1.1.1 (the dev container's own resolver negative-caches, which is a local quirk, not a DNS problem).
- **Step 7** — Both API keys smoke-tested with `curl`: Anthropic `/v1/messages` (claude-sonnet-4-6) → HTTP 200; Voyage `/v1/embeddings` (voyage-3-large) → HTTP 200, **1024-dim** output (confirms the Qdrant vector size for Phase 1).
- **Step 8** — Real `.env` created in the dev container (gitignored, verified not tracked). Non-secret values set; `BASIC_AUTH_USER=demo`, bcrypt hash for password `demo` generated with `htpasswd -nbB` and verified; single `$`, not doubled. API keys filled in by the user and verified working (step 7 above).
- **Steps 9–10** — Scaffolding committed (`.devcontainer/*`, `.gitignore`) as commit `001` and pushed to `origin` (`git@github.com:kaykim81/oracle-knowledge-navigator.git`). `main` in sync with `origin/main`.

**Deviations from the plan (with reasoning):**

- **Develop-here / deploy-to-VPS split.** We author and commit code/config/docs in the dev container (`/workspaces/oracle-knowledge-navigator`) and deploy to the VPS (`/docker/oracle-knowledge-navigator`) separately. Documented in `CLAUDE.md`, `MASTER_PLAN.md` (Phase 0 callout), and `ARCHITECTURE.md`. VPS-side actions (SSH, real `.env`, live URL) are performed at deploy time.
- **Handover docs intentionally gitignored.** `.gitignore` excludes `CLAUDE.md`, `PROJECT_CONTEXT.md`, `ARCHITECTURE.md`, `MASTER_PLAN.md`, so the planning/interview docs stay local and out of the public repo. This deviates from step 9's literal "commit the four handover files," but is deliberate — `PROJECT_CONTEXT.md` contains the job description and interview strategy.
- **Tooling.** `htpasswd` (apache2-utils) was not preinstalled in the dev container; installed it to generate the bcrypt hash. `dig`/`host`/`nslookup` are also absent — DNS checks used Python/raw resolver queries instead.

**Deferred / user-owned, not yet confirmed:**

- **Spend caps.** The plan calls for $20 hard caps on the Anthropic and Voyage dashboards. This is a dashboard action owned by the user — not verifiable from the dev container. **Confirm before any eval runs.**
- **`.env` on the VPS.** The real `.env` currently exists in the dev container. It must be replicated on the VPS at deploy time (the runtime target). Weak `demo/demo` basic-auth is acceptable for a demo but trivially guessable on a public URL; spend caps are the real backstop.
- **`.env.example`** is authored but not yet committed (untracked). Commit it with Phase 1 work.

---
