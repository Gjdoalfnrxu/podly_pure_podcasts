# Dated experiment runs

`scripts/experiments/run_daily_loop.py` writes one directory per UTC date:

```
docs/experiments/runs/YYYY-MM-DD/
  summary.json
  summary.md
  spend.json
  H001.json
  …
```

Confirm disk cache lives in `docs/experiments/runs/.cache/` (gitignored).
Golden staging lives in `docs/experiments/golden/staging/` (gitignored).

`--check` (CI) should point `--runs-dir` at a temp folder so pytest/CI
does not dirty the git tree. Agents running the daily loop for real may
commit `summary.md` / hypothesis JSON updates, never caches or keys.
