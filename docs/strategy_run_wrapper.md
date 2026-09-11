# Strategy Run Wrapper

## Decision

Strategy execution uses a static, versioned wrapper.  The LLM may understand a
user request and may compile a small runtime profile when a strategy revision is
created, but it does not generate orchestration code for each run.

The user-owned strategy remains responsible only for business logic.  The host
owns server-resolved requester identity, pinned asset loading, trading-session
resolution, universe snapshots, bounded dispatch, progress events, and run
evidence.  Owner, revision, and cutoff never come from public Tool inputs.

## SOFT -> HARD -> SOFT

1. SOFT: understand the requested strategy, stocks or market, date, and output.
2. HARD: `StrategyRunResolver` freezes the strategy reference, completed
   trading session, run window, warm-up window, and point-in-time universe.
3. HARD: `StrategyInvocationAdapter` derives scalar map, native array, or one
   universe-native call from the system-owned runtime profile and input schema.
4. HARD: `StrategyRunExecutor` runs independent calls with bounded concurrency
   and emits progress events while preserving universe order.
5. SOFT: a financial renderer explains and presents the results.

There is deliberately no public `single` / `multi` / `market` mode.  One stock
is a one-member universe.  A market is a point-in-time universe reference.

## Runtime profile

The profile is a system-owned companion to a strategy revision and is not part
of user business inputs.  The creation host must compile it once, validate it,
and persist it atomically with that revision:

```json
{
  "protocol": "strategy_runtime_profile.v1",
  "binding": {"field": "stock_code"},
  "required_history_sessions": 20,
  "default_run_sessions": 100,
  "default_universe_ref": {"type": "all_a_share"},
  "market_code": "CN_A"
}
```

An empty binding only means the Wrapper does not inject a security argument; it
does not prove that the strategy consumes the complete frozen universe.  If a
binding exists, its JSON Schema type controls dispatch: a string becomes
independent mapped calls and an `array<string>` becomes one native batch call.
The first selection/backtest bridge should prefer an explicit `array<string>`
binding so scope enforcement remains observable.

For newly created Custom Tool strategies, Coding emits this optional companion
outside the public `tool_contract`; the system canonicalizes it and stores it in
the immutable revision `spec_json`.  Presence derives the internal `strategy`
capability.  Ordinary Tools omit the companion and keep their existing
contract.  Local implementation-only edits inherit it, while a full redesign
must emit a fresh companion so stale execution or backtest metadata cannot
survive a changed strategy shape.

Cross-sectional and shared-portfolio selectors should normally declare an
`array<string>` binding.  This lets the Wrapper inject one frozen point-in-time
universe and preserves a single ranking context.  A scalar string binding is
reserved for rules that can truly be evaluated independently per security.

`run_sessions` is the reported/evaluated period.  `required_history_sessions`
is warm-up data before that period and must not silently extend reported
returns.  `as_of` defaults to the latest completed session, not simply today's
calendar date.

`binding` and necessary warm-up are revision execution facts.  The default run
length and default universe are fallbacks: the user's current request may
override them, and changing a product UI default should not by itself require a
new strategy revision.

Date naming is stable across layers: compatibility input may use `as_of` or
`as_of_date`; the authoritative resolved runtime fact is `effective_as_of`; a
Tool result records `output_date`.  These are not interchangeable.

## Backtest boundary

`ResolvedStrategyRunPlan.backtest_context()` maps to the current standalone
backtest without changing its core:

- load market data from `data_start` through `end_date`;
- start performance and decisions at `start_date`;
- pass the frozen universe to `BacktestConfig`;
- keep the shared-cash portfolio's trading-day loop sequential.

Independent stock evaluation, data loading, and separate scenarios may run in
parallel.  A cross-sectional strategy or a shared portfolio must not be split
into independent stock runs.  The current executor bounds one run; the process
host must still provide a shared queue/semaphore and backpressure before market
scope is enabled for concurrent users.

### Selection Tool bridge

`src/services/strategy_backtest_service.py` provides the first isolated bridge
from a selection Tool to the existing `BacktestEngine`:

1. the parent `ResolvedStrategyRunPlan` freezes the evaluation window and
   universe;
2. each sequential backtest decision date produces a one-session child plan,
   so the Tool host receives that date as `effective_as_of`;
3. `SelectionOutputProfile` maps one explicit ranked result array into a
   `selection_snapshot.v1` without searching arbitrary output fields;
4. `EqualWeightSelectionPolicy` applies Top-N, exposure, and rebalance cadence
   outside the user Tool;
5. the engine turns the complete target into next-session-open orders.

The current bridge accepts only parent plans whose universe reference is
`explicit_targets`.  Reusing a final-date `universe_ref` snapshot across earlier
decisions creates survivor and new-listing bias, so dynamic scopes fail with
`historical_universe_timeline_required` until a daily timeline is frozen and
consumed by both data loading and strategy visibility.

The first bridge deliberately accepts only one native ranked-result invocation.
It rejects scalar-per-stock outputs and paths that resolve to several ranking
blocks before using them.  Combining scalar factor values, choosing between
several windows, or merging several groups requires a separately declared
aggregation policy; invocation order is never treated as a ranking.

A successful empty ranked array means a complete empty target and therefore
cash.  A Tool failure is an exception and is never reinterpreted as an empty
selection.

Historical replay also requires one host preflight proving authorization,
pinned asset identity, and point-in-time enforcement.  The host must return a
stable `asset_fingerprint`.  The bridge result adds an execution fingerprint
over that asset fingerprint, the engine run fingerprint, and every daily
selection-result fingerprint.  This is the relevant replay identity for an
external Tool strategy; the engine fingerprint alone does not include Tool
outputs.

Here `point_in_time` is a data-visibility guarantee at the finance Host
boundary.  It is not a claim that arbitrary Tool code cannot read a clock or
use randomness.  Such nondeterministic inputs must be removed or explicitly
frozen before a strategy is advertised as reproducible; result fingerprints
detect differing outputs but do not make them deterministic.

`selection_output_profile` addresses the stable Tool host result envelope.
Candidate paths therefore normally look like `data.<public output field>`; the
leading `data` is the host envelope and must not make business output add a
second artificial `data` object.

The initial integration test uses the existing active
`quant_factor_screening` implementation with its actual Provider, Python factor
calculation, and `data.selected_stocks[].stk_code` output contract against
controlled point-in-time data.  The definition's previously empty `universe`
schema is now explicitly `array<string>`, allowing the Wrapper to make it the
single frozen source of scope.  This is a production-equivalent fixture test,
not an online replay of an active user asset.  A second vertical test now runs a
revision-scoped Custom Tool through the actual Runtime protocol and historical finance
bridge; its provider result is controlled so cutoff behavior is deterministic.

## Entry contract

`scope.targets` accepts canonical security identifiers such as `600519.SH`.
Natural-language names such as `贵州茅台` belong to the upstream
`StockIdentityResolverService`; the wrapper uppercases and de-duplicates
identifiers but intentionally does not perform fuzzy identity resolution.

Tool and Skill execution must be supplied by one host asset invoker that has
already enforced lifecycle, visibility, server-resolved requester authorization,
and pinned revision loading.  Asset owner and run owner are distinct facts; no
client `owner` field is trusted.  The wrapper fails closed when such a host is absent; it does
not call low-level `SkillRunner` or the generic registry as an authorization
bypass.  This module treats the injected port as trusted; the concrete
authorization and pinned-revision host remains outside the wrapper.

## Current integration boundary

The run Wrapper is isolated in `src/services/strategy_run_service.py`, and the
selection/backtest bridge is isolated in
`src/services/strategy_backtest_service.py`.  They include real adapters for
`aiia_trade_calendar`, historical all-A-share membership from
`kcrp_stock_baseinfo`, and the existing standalone BacktestEngine.  No product
route is exposed until the historical Tool host and user-run ownership satisfy
the product contract.

`src/services/custom_tool_historical_replay_host.py` now provides the first
internal Custom Tool host vertical slice.  It owner-checks and freezes an exact
revision, revalidates the strategy companions, and runs the cached bundle without
re-reading the active pointer.  Its finance boundary currently allows only
`stock.quote.dynamic_cal`, forces historical mode and an inclusive raw-data
cutoff, constrains execution to the Wrapper's explicit symbols, and only exposes
raw trade-date-bound quote columns.  Current base-info names, ST/delisting
labels, minute fields, and retrospectively adjusted columns fail before provider
execution.  This is verified through the Custom Tool Runtime and backtest
bridge, but it is not a public route or a point-in-time claim for every Catalog
API.
The production Host never executes the asset's development-time `local_dev`
backend.  It selects an available formal sandbox as a server-owned runtime fact
and fails closed when none exists, so strategy code cannot bypass the finance
Host through direct network or workspace access.  The local execution used by
the integration test is explicitly controlled and is not product isolation.
The online `dynamic_cal` provider also generates calculation code at request
time.  Its data projection is cutoff-safe, but the generated logic is not yet
pinned to the strategy revision.  Product replay must freeze and fingerprint
that code, or prefer a deterministic raw-data API; equal task text alone is not
a reproducibility guarantee.

Before enabling historical replay for arbitrary existing strategies, four host
or creation bridges still need to be completed deliberately:

1. expand the current API-specific historical finance policy through Catalog
   declarations.  Only APIs whose true availability semantics can be enforced
   may enter replay; recording an as-of value alone does not prevent a provider
   from reading a later row;
2. connect the pinned Custom Tool host to a user-owned persistent run service
   with limits, cancellation, recovery, and result ownership checks;
3. backfill or explicitly redesign older strategy revisions that predate the
   optional `strategy_runtime_profile.v1` companion.  New Coding revisions now
   persist it, but existing assets are intentionally not guessed from names or
   source text.
4. preserve owner context in the existing Skill-to-Tool path before a Skill can
   safely invoke a user's private custom tool.

Those bridges belong to the runtime host.  They must not add fields or helper
loops to user strategy code.
