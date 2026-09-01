# Local stress harness

`run_stress.py` drives N back-to-back G30 → G60 conversions with post-write verification (`verify_conversion.py`). It is **not** part of the GUI exe.

Run output (`_tmp_stress/runs/`, `_tmp_stress/runs_*/`) is gitignored. Keep only this script and this README in git.

## Quick check (synthetic fixtures)

Uses copies of the bundled G60 bases rewritten as G30 identity — no site files required:

```powershell
python _tmp_stress/run_stress.py -n 20 --work-dir _tmp_stress/runs
```

## Production 500-run (matches `docs/STRESS_TEST_REPORT.md`)

Site G30 files are gitignored. Point at local copies:

```powershell
python _tmp_stress/run_stress.py -n 500 `
  --work-dir _tmp_stress/runs_mixed `
  --g30-urs "C:\path\to\Publix 1602 G30.urs" `
           "C:\path\to\HCHPublix Firmware7-6_480V2000A 3-20-18.urs" `
  --g30-xml "C:\path\to\HCHPublix Firmware5-9_208V3000A 1-18-13.xml" `
            "C:\path\to\Publix 1563 Cocoa Beach.xml" `
  --report-md docs/STRESS_TEST_REPORT.md
```

The last committed 500-run report was generated against converter **1.0.6**. Current converter is **1.0.9**. Re-run the command above after a converter change if you want the report version line to match `aio/version.json`.
