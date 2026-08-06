# Engineering Review — 2026-08-05

A whole-codebase architecture review, requested explicitly as a "lead
architect" pass: read every major subsystem end to end, evaluate strengths/
weaknesses/technical debt/scalability, and answer one question directly —
given everything already built, what is the next major architectural leap?
No code was written for this review; it is pure analysis, produced the
session immediately after `strategy/context_gate.py` (`ContextGatedStrategy`,
see `CHANGELOG.md`'s 2026-08-04 entry) — the first real consumer of the
Market Context Engine.

**Method:** direct reading of `CLAUDE.md`, `PROJECT_STATE.md`, `ROADMAP.md`,
`KNOWN_ISSUES.md` (all 44 entries), `docs/ARCHITECTURE.md` in full;
line-by-line review of `engine.py`, `strategy/` (all four reference
strategies + base), `risk/manager.py`, `context/` (models + engine),
`research/safety.py`, `research/optimizer.py`, `backtest/metrics.py`,
`backtest/context_comparison.py`, `models.py`; four parallel investigations
covering the API layer (`app.py`, `routes/`, `services.py`, `jobs.py`,
`live_session.py`), the broker/journal/state/contracts layer, the
`research/`+`research_server/` layers, and `market_data/`/`config.py`/`db/`/
`frontend/`. Repo total at time of writing: ~32.9k lines backend, ~12k
frontend, ~24.6k tests (~69.4k lines).

## Overall architecture

```
FRONTEND (React/Vite, 21 pages, 25 components, 119-fn 1:1 api.ts mirror, no state library)
    |
API (FastAPI, 17 route files / ~139 endpoints, zero auth, wide-open CORS)
    |
CORE ENGINE (config -> engine.TradingEngine -> strategy -> risk -> broker -> journal)
    | (one-way, tested not just asserted)
CONTEXT ENGINE (8 dimensions, fully built, integrated via ContextMode, one real
    consumer as of 2026-08-04: strategy/context_gate.py)
    |
RESEARCH LAYER (optimizer, safety, ML pipeline, trade_store -- 6,853 lines)
    |
PERSISTENCE (SQLite dual-file default / TimescaleDB team-mode, selected transparently
    by one env var; 25 tables total across both schemas)

ALONGSIDE, NOT INSIDE THE PIPELINE:
COLLABORATION/ACCOUNTS (work items, overlap detection v1+v2, merge readiness,
    Worker Registry, Integration Queue, persisted Integration Reviews, three
    background schedulers -- 4,837 backend lines + 1,552 frontend lines)
```

The one-way dependency direction (`context/` never imports `engine.py`/
`risk/`/`brokers/`; `research_server/` has exactly one documented import from
`api/`, confirmed by grep) is real, not aspirational — enforced by dedicated
tests, not just a diagram. The single biggest structural decision in this
codebase — one `TradingEngine`, one `Strategy` interface, driven identically
by backtest replay and live/paper polling — is sound and is the reason a
whole category of backtest-live divergence bugs simply cannot occur here.

## Strengths

- **The engine/strategy/risk/broker separation is textbook and actually
  enforced.** `Broker.submit_bracket`'s docstring requires the stop be a real
  resting order in the same call or the adapter "should raise rather than
  quietly fall back to an in-memory one" (`brokers/base.py:6-12`) — a
  contract about *failure mode*, not just a method signature.
  `RiskManager.can_enter` orders its checks "cheapest and most absolute
  first" specifically so logged block-reasons are the most useful one
  (`risk/manager.py:3-6`).
- **Extension points are genuinely additive, not bolted on.** `ContextMode`
  (OFF/OBSERVE/ENABLED), `Strategy.uses_context`, `TradingEngine.signal_filter`,
  `StrategyRegistry` — every one of these was designed so the default
  behavior is bit-for-bit identical to before the extension point existed,
  and that's *tested* (`test_engine_context_integration.py`'s
  `TestExistingBehaviorUnchanged`), not just claimed.
- **"Compute live, never persist" is a real, consistently-applied
  architectural principle** — `context/`, `collaboration/overlap.py`/
  `overlap_v2.py`/`merge_readiness.py`/`git_info.py`/`context_bundle.py`,
  `connected_users.py` all recompute from source rather than caching. The one
  deliberate exception (`integration_reviews`, an append-only table) is
  explained in its own docstring as a conscious trade-off, not a lapse.
- **Bug-hunting here is systemic, not one-off.** ISSUE-016's check-then-set
  race was found once, then the same pattern was deliberately searched for
  and found in two more places (`paper_trader.py`, `orchestrator.py`,
  `live_session.py`) and fixed identically in all three. ISSUE-020 was found
  by auditing *every* singleton accessor after one was found unlocked.
- **Documentation is load-bearing and it works.** This entire review was
  reconstructed to near-complete accuracy from `PROJECT_STATE.md`/
  `ROADMAP.md`/`docs/ARCHITECTURE.md`/`KNOWN_ISSUES.md` before reading a
  line of code, and every claim those docs made checked out against direct
  code reading.
- **Skepticism is structural, not cultural.** `research/safety.py` is wired
  into the actual optimizer path (`build_safety_report`), forcing a "why
  shouldn't I trust this" report on every result rather than leaving it to a
  human's discipline.

## Weaknesses / technical debt

| Issue | Evidence | Status |
|---|---|---|
| `journal.py`'s decision log unbounded on the write side | 34.2M lines / 9.2GB observed (ISSUE-017); the *reader* was fixed, `journal.py::_write` is still a bare unbounded append, no rotation, no thread lock | Open, worsening |
| Two god-objects | `api/services.py`: 1,871 lines / 21 sections / 85 top-level defs. `research/trade_store.py`: 1,467 lines / ~65 public methods / 12+ concerns | Open, growing |
| Zero authentication on 139 endpoints | `api/app.py:230-235` CORS `allow_origins=["*"]`; `accounts/`'s `api_key` generated, checked against nothing (`routes/accounts.py:1-8`) | Open — correctly flagged in ROADMAP's Future section, but its "internal tool" justification erodes as Team Mode's real multi-device target approaches |
| `api/jobs.py` doesn't survive a restart | Job state persists to the `jobs` table; the `ThreadPoolExecutor` itself (4-worker cap) is in-memory — a restart abandons in-flight jobs, rows stuck `running` forever, no reaper | Open |
| `TradovateBroker` never verified against a real account | Own docstring: "field names are believed correct but unverified," mocked-HTTP tests only, no retry/reconnect on transient HTTP failures | Open — sits at the highest-stakes code path in the system |
| `bars` schema drift (ISSUE-004), `US80Z` OHLC violations (ISSUE-005) | Diagnosed, correctly deferred pending schema-change approval per CLAUDE.md §8 | Open, low urgency |
| Frontend timestamp bug (ISSUE-021) | `format.ts::dateTime()` parses SQLite local-mode timestamps as local instead of UTC | Open, deliberately deferred |
| `npm run dev` broken on Windows (ISSUE-006/007) | Self-inflicted `taskkill /F /IM node.exe`; worked around by `start.ps1` | Open, cosmetic |
| No CI | Tests only run when a session runs them by hand | Open |

Notably *absent*: broad `except Exception` patterns were checked for
sloppiness and found to be the opposite everywhere sampled
(`_build_market_context`, `_safe_signal`, per-strategy isolation in
`paper_trader.py`, per-target isolation in `scheduler.py`) — every one logs,
counts, and is deliberately scoped.

## Hidden opportunities

1. **`ContextScorer`'s `[0,1]`-score-plus-reason shape is a template**, not a
   one-off — the same shape could back position-sizing confidence,
   strategy-selection confidence, or a `collaboration/`-side merge-confidence
   score later. Resist building any of those until Phase 1's mechanism has a
   real validated hypothesis behind it.
2. **`research/safety.py` and `TradovateBroker`'s verification gap are the
   same underlying problem wearing two hats** — this codebase is very good
   at validating *strategy* claims and thin on validating *infrastructure*
   claims about the live-trading path. A "safety.py for brokers" (a
   scripted, repeatable verification pass against a real Tradovate demo
   account) would close the platform's riskiest unverified claim using the
   posture already applied everywhere else.
3. **TimescaleDB is already provisioned for exactly the problem `journal.py`
   has.** The decision journal's unbounded growth doesn't need a bespoke
   rotation scheme invented from scratch — it needs to become another
   hypertable.
4. **The ML win-probability `signal_filter` and a context-based veto are the
   same seam.** Nobody has yet asked whether a strategy's ML-predicted win
   probability and its market-regime confidence agree or disagree — a free
   cross-check sitting at one call site (`_build_deployed_signal_filter`)
   once both signals exist there.

## Scalability concerns / future bottlenecks

- SQLite's measured concurrent-writer ceiling (~100 threads, per the SIL
  Phase 6 audit) — directly relevant once Team Mode's real multi-device
  usage (not live yet per CLAUDE.md §2A) generates genuine concurrent writes.
- `api/jobs.py`'s 4-worker cap will bind once nightly jobs, ad-hoc
  user-submitted backtests, and multiple simultaneous Team Mode users
  compete for the same pool — no backpressure or queueing visibility today.
- `journal.py`'s per-write file-open-append with no buffering scales
  linearly worse as more parallel autonomous strategies log every decision.
- Frontend has no shared client cache (plain React state, confirmed via
  `package.json` — no Redux/Zustand/React-Query). Fine at 21 pages; will
  start to show as Mission Control's many polling panels multiply.
- `decisions.jsonl`'s 9.2GB and climbing is the most concrete, already-
  measured bottleneck in the entire system — on disk today, not hypothetical.

## Responsibility boundaries

Mostly excellent, worth naming as a model: strategy proposes / risk disposes
(a strategy literally cannot reach a broker); `context/` describes / never
decides (enforced by import-boundary tests); brokers execute / never see
`context/` at all; `collaboration/` coordinates people and AI sessions
working on the *code*, entirely orthogonal to the trading pipeline it sits
beside. The two blurred boundaries are the two god-objects above.

## Extension points (use these, don't build parallel ones)

`ContextMode`/`uses_context`/`self.context`; `TradingEngine.signal_filter`;
`StrategyRegistry.register`; `Broker` ABC (with its explicit "raise, don't
silently degrade" contract); `ContextScorer`/`ContextScore`; the
`collaboration/` "compute live from git + DB, never a stale cache" pattern;
`get_store()`/`get_market_data_store()`'s transparent SQLite<->Postgres
factory-function branching as the template for any future dual-backend need.

## Areas of unnecessary complexity

Not code to delete — CLAUDE.md's own non-negotiables forbid that, and
nothing here is broken — but a place to **stop adding**, said plainly:
`collaboration/` + `accounts/` backend is **4,837 lines** (3,125 + 541 + 377
routes + 794 services), plus **1,552 lines** of frontend — **6,389 lines
total** of tooling built to coordinate *people building the platform*. That
is within a few hundred lines of `research/` + `research_server/` combined
(**6,853 lines** — the actual trading-research engine) and **2.4x the size
of the entire strategy library** (`strategy/`, 2,622 lines: four reference
strategies + base + one shared exits module). Six SIL phases, two
overlap-detection engines (V1 and V2, both still live), three background
schedulers, a Worker Registry, an Integration Queue, and a persisted
Integration Review pipeline exist to coordinate work on a platform that has
not yet validated a single strategy's edge. Every individual SIL milestone
is well-scoped and well-tested — this is a portfolio-level imbalance, not a
defect in any one piece: the meta-layer has been engineered to a degree of
sophistication the product it's coordinating hasn't earned yet.

## Areas that should never change

Exactly CLAUDE.md §8's protected list: database schema (25 tables, two
dual-backend stores, migration-managed), API routes (139 endpoints, a 1:1
frontend mirror depends on exact shapes), the `Strategy` interface
(`on_bar`'s signature is the one thing every reference strategy,
`ContextGatedStrategy`, and the ML signal filter all agree on), and the
backtest engine (`TradingEngine` — the single-codebase-for-backtest-and-live
guarantee is the platform's core correctness property).

---

## The question that matters: what's the next major architectural leap?

Not a new subsystem. **This platform has built an exceptional amount of
infrastructure — a Context Engine, a coordination platform, a Team Mode
deployment path, an ML workstation — around a trading core that has never
been asked to prove itself.** Four reference strategies exist. None has
cleared `research/safety.py`'s own bar. The Context Engine sat fully built
and fully integrated for over a week with zero consumers before
`ContextGatedStrategy` landed 2026-08-04. That is the real architectural
gap, and it's not filled by another box on the diagram — it's filled by
finishing the vertical slice that already exists in pieces.

Ranked by leverage:

**1. Close the Context -> Strategy -> Validation loop for real (the product
leap).** `ContextGatedStrategy` + `compare_context_impact` (built
2026-07-27) + `research/safety.py` already form a complete, tested pipeline
from hypothesis to skeptical verdict. Nothing has ever run through it
end-to-end on a real trading claim. Highest leverage because every other
subsystem in this repo is infrastructure *for* this outcome.

**2. Real authentication (the structural leap).** Already correctly
identified in ROADMAP's Future section as the smallest next step given the
`api_key` primitive already exists — ranked here, not discovered here. The
one piece every "Team Mode is the target" ambition structurally depends on
the moment a second real device actually joins.

**3. Move the decision journal onto real infrastructure (the
debt-retirement leap).** `journal.py`'s unbounded JSONL is the platform's
most concrete, already-measured structural liability, and TimescaleDB is
sitting right there unused for it.

**What not to do next:** add a fourth SIL phase, a fifth background
scheduler, or any new collaboration-tooling milestone, until #1 has actually
run once. The coordination layer doesn't need to shrink — CLAUDE.md forbids
removing working systems, correctly — but it has no further claim on the
next unit of engineering effort until the thing it's coordinating has
produced a result worth coordinating around.

## Prioritized 12-month roadmap (architecture, not features)

**Months 1-2 — Prove the core loop (Leverage #1).** Pick one strategy + one
pre-registered hypothesis, run it through `ContextGatedStrategy` ->
`compare_context_impact` -> `build_safety_report`. Whatever the verdict,
document it as this platform's first real validated-or-rejected trading
claim.

**Months 2-3 — Retire the journal risk (Leverage #3).** Bounded/rotated or
TimescaleDB-hypertable-backed decision log, reusing the existing
team-deployment Postgres investment. Schema-adjacent — needs CLAUDE.md §8
sign-off.

**Months 3-5 — Real authentication (Leverage #2).** Bearer-token validation
of the existing `api_key` as request-scoped identity; wire
`accounts/permissions.py`'s already-built role->capability table into actual
server-side enforcement.

**Months 5-6 — Verify the live-trading path.** A `TradovateBroker`
verification pass against a real demo account.

**Months 6-8 — Split the two god-objects.** `api/services.py` -> per-concern
modules; `research/trade_store.py` -> per-entity stores behind the same
public interface. Reduces the false-positive overlap-warning rate
`collaboration/overlap_v2.py` already exists to manage.

**Months 8-10 — Job-system durability.** A reaper for orphaned `running`
`jobs` rows after a restart; reconsider the in-memory `ThreadPoolExecutor`
once real concurrent load from Team Mode usage is observed.

**Months 10-12 — Second and third validated (or rejected) strategies.**
Only after two or more strategies have independently cleared validation is a
real capital-allocation/orchestration layer (deliberately rejected as
premature in the 2026-08-04 architecture review) worth reconsidering.

Throughout: no new `collaboration/`/SIL milestones unless a concrete
coordination failure is actually observed (not anticipated) once more than
one contributor is genuinely working here at once.
