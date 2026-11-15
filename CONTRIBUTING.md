# Contributing

Thanks for interest! This is a deliberately small, dependency-free codebase —
read `docs/constitution/` (the 12 engineering iron rules) first; they govern every change.

## Dev setup (no install needed)

```bash
git clone https://github.com/<you>/quantforge.git
cd quantforge
python -m unittest discover -s tests   # 129 tests, all stdlib
python qf.py demo                      # offline five-things smoke
python qf.py gate                      # the one-command quality gate (must stay 5/5)
```

## Ground rules

1. **Zero third-party dependencies** for the core engine (D-008). `akshare` is an optional
   data-acquisition layer only (`qf fetch-real`) and must never be imported at module load.
2. **Per-trade conservation checks must stay zero-failure.** Any change that makes
   `conservation_failures > 0` on the sample pool is a bug, not a trade-off.
3. **Backends must stay bitwise-identical** (serial / parallel / remote — see
   `tests/test_w8.py`, `tests/test_w9.py`). Parallelism changes execution location, never computation.
4. **User strategies run in the sandbox** (`qf verify-strategy`): import whitelist, process
   isolation, runtime audit hooks, timeout. The sandbox goal is to catch *mistakes*, not attackers.
5. **Never ship real market data.** `data/kline_real/` is gitignored; real quotes are fetched
   by each user under their own data-provider terms (D-010).
6. UI changes go through the taste gate: `lint.mjs` / `inspect.mjs` with 0 FAIL before merge.

## Three immutable product lines (S51)

No auto-trading. No promised returns. No hidden configuration.
Any feature request touching these is rejected at the review gate, not in design.
