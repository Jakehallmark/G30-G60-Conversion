# G30 to G60 Relay Settings Converter

Converts GE Multilin UR series **G30** relay settings to **G60** format via a standalone Windows GUI. Output is a ready-to-import G60 `.urs` (default) or `.xml` plus an HTML conversion report for the XML workflow.

## Reliability validation

**[Stress test report →](docs/STRESS_TEST_REPORT.md)**

| Metric | Result |
|--------|--------|
| Current converter | **v1.0.9** |
| Consecutive conversions | **500 / 500 passed** |
| Workflows | URS + XML (alternating) |
| Site files | 4 production G30 exports (FW 5.9x – 7.6x) |
| Verification | Post-write register check on every run |
| Last 500-run report | v1.0.6 (see report for later converter notes) |

Each [GitHub Release](https://github.com/Jakehallmark/GE-Multilin-UR-G30-G60-Conversion/releases) includes the stress test summary, SHA256 hash for IT allowlisting, and the full report as a downloadable asset.

**Technical reference:** [Conversion Process — step-by-step guide](CONVERSION_PROCESS.md)

## Repository layout

```
├── convert_g30_to_g60.py          # XML converter engine (used by the GUI; dev CLI)
├── convert_g30_to_g60_urs.py      # URS converter engine (used by the GUI)
├── urs_io.py                      # URS read/write helpers
├── verify_conversion.py           # Post-write register check
├── bases/                         # G60 firmware templates (committed)
│   ├── G60 Base.xml / .urs        #   default (firmware 860)
│   ├── G60 Base [8.4x].xml / .urs
│   ├── G60 Base [8.5x].xml / .urs
│   ├── G60 Base [8.6x].urs
│   └── G60 Base [8.7x].xml / .urs
├── firmware/                      # Analogoperand CSV maps (bundled into the exe)
├── aio/                           # GUI source + build.ps1 / release.ps1
├── _tmp_stress/                   # Optional local stress harness
│   └── run_stress.py              #   run output under runs*/ is gitignored
├── docs/
│   └── STRESS_TEST_REPORT.md      # 500-run validation report (committed)
├── release/                       # Local build output — exe is not committed
├── README.md
└── CONVERSION_PROCESS.md
```

---

## Quick start (end users)

1. Download **`G30-to-G60-Converter.exe`** from [GitHub Releases](https://github.com/Jakehallmark/GE-Multilin-UR-G30-G60-Conversion/releases).
2. Run the exe — **no Python install required** (the exe bundles its own runtime).
3. Select your G30 settings file, pick **target firmware**, and convert.
4. Output goes to **`Desktop\G60_Conversion\`** (`.urs` for URS workflow, or `.xml` + `_OR.html` for XML workflow).

---

## IT deployment (BeyondTrust / Intune)

The release exe is **not** Authenticode-signed. Company IT should allowlist each version by **SHA256 file hash**.

**Use the hash on the [GitHub Release](https://github.com/Jakehallmark/GE-Multilin-UR-G30-G60-Conversion/releases) page** for the version you deploy. GitHub Actions builds the exe, computes the hash from that build, and publishes it on the release notes automatically.

> **Important:** A local `.\aio\build.ps1` produces a **different** exe (and SHA256) than CI. Do not allowlist a hash from your machine for end-user deployments — wait for the Release workflow to finish and copy the hash from the release page.

To verify a downloaded exe yourself:

```powershell
Get-FileHash G30-to-G60-Converter.exe -Algorithm SHA256
```

Compare the result to the hash on the matching GitHub Release page.

---

## Requirements

| Use case | Python on target PC |
|----------|---------------------|
| **`G30-to-G60-Converter.exe` (GUI)** | No — standalone exe |
| **Building the GUI from source** | Yes — Python 3.10+, Flet, PyInstaller |

- **`bases/G60 Base*.xml`** and paired **`G60 Base*.urs`** must be present in the repo/build — one template set per supported target firmware
- G30 and G60 settings can be **UTF-8 or UTF-16 LE** (auto-detected)
- Windows 10/11 64-bit for the GUI exe

**Do not commit G30 source files** — they contain site-specific relay configuration.

### GUI troubleshooting

If the exe crashes on first launch with **`Access is denied`** under `C:\Users\<you>\.flet\client\`, that is a Flet cache permissions issue (not a missing Python install). Rebuild from the latest source or use a current GitHub Release — the app extracts its desktop runtime to `%LOCALAPPDATA%\G30-to-G60-Converter\` instead.

---

## Rebuilding the release exe

```powershell
pip install pyinstaller -r aio/requirements.txt
.\aio\build.ps1
```

Writes `release/G30-to-G60-Converter.exe` with all templates from `bases/` bundled inside. Local builds print a SHA256 for smoke testing only — **production allowlisting uses the hash from the GitHub Release page** (CI-built exe).

Run the GUI locally without building:

```powershell
pip install -r aio/requirements.txt
python aio/app.py
```

### Developer CLI (optional)

The conversion engines can be run directly for debugging — end users should use the GUI exe instead:

```batch
python convert_g30_to_g60.py "MySite G30 480V.xml" "C:\Work\Converted"
python convert_g30_to_g60_urs.py --help
```

---

## Base templates

| XML template | URS pair | Typical firmware |
|--------------|----------|------------------|
| `bases/G60 Base.xml` | `G60 Base.urs` | 860 (default; also `G60 Base [8.6x].urs`) |
| `bases/G60 Base [8.4x].xml` | `G60 Base [8.4x].urs` | 840 |
| `bases/G60 Base [8.5x].xml` | `G60 Base [8.5x].urs` | 850 |
| `bases/G60 Base [8.7x].xml` | `G60 Base [8.7x].urs` | 870 |

Export each blank template from UR Setup on the matching relay firmware. The converter preserves each template's `version` and `orderCode` in the output. Analogoperand CSV maps live in `firmware/` and are bundled into the GUI exe.

There is no `G60 Base [8.6x].xml` — firmware 8.6x uses the default `G60 Base.xml`.

---

## GitHub Releases

Pushing a version tag runs [`.github/workflows/release.yml`](.github/workflows/release.yml), which builds the GUI exe, generates release notes (stress test results, SHA256 hash, download instructions), and publishes a **GitHub Release** — no manual release notes required.

### Publish a new version

**Recommended** — one command bumps the version, tags, pushes, and builds the exe:

```powershell
.\aio\release.ps1
```

This updates `aio/version.json`, **commits all pending changes** (not just the version file), creates a tag like `v1.0.9`, pushes to GitHub (which triggers CI to attach the exe to the Release), and runs a local build to `release/G30-to-G60-Converter.exe`.

Options:

```powershell
.\aio\release.ps1 -Version 1.0.9    # explicit semver (next build number auto-increments)
.\aio\release.ps1 -Bump minor         # bump minor instead of patch
.\aio\release.ps1 -SkipPush           # commit + tag + build locally only
.\aio\release.ps1 -SkipBuild          # tag/push only; CI builds the exe
```

Versioning: `aio/version.json` is the single source of truth. The Git tag matches `version` (e.g. `v1.0.9`). Windows exe **File version** uses four parts: `1.0.9.<build>` where `<build>` increments on rebuilds of the same release.

Manual flow (if you prefer):

```powershell
git add .
git commit -m "Your changes"
git push origin main

git tag v1.0.9
git push origin v1.0.9
```

When the Action finishes (green checkmark), open **Releases** — you should see `G30-to-G60-Converter.exe` as a downloadable asset (not just source zip/tar.gz).

### Fix a tag that has no exe

If a tag exists but the Action failed (red X), the tag page only shows source archives. After fixing the workflow:

```powershell
git tag -d v1.0.2
git push origin :refs/tags/v1.0.2
git tag v1.0.2
git push origin v1.0.2
```

Or push a new patch tag (`v1.0.3`).

### Download for end users

**[Releases](https://github.com/Jakehallmark/GE-Multilin-UR-G30-G60-Conversion/releases)** → pick a version → download **`G30-to-G60-Converter.exe`**.

---

## Important notes

See [CONVERSION_PROCESS.md](CONVERSION_PROCESS.md) for matching rules, FlexLogic translation, HTML report sections, and troubleshooting.

**Zero Invalid Settings** in UR Setup after import is the target. Invalid Settings indicate a value format or firmware-code problem — check the HTML report and confirm the base template matches the target relay firmware.
