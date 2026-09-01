# Release folder

The prebuilt **G30-to-G60-Converter.exe** is not committed to git.

- **Download** the latest (`v1.0.9` as of this writing) from [GitHub Releases](https://github.com/Jakehallmark/GE-Multilin-UR-G30-G60-Conversion/releases) — each release includes the CI-built exe and its SHA256 on the release page.
- **Build locally** with `aio/build.ps1` (writes `release/G30-to-G60-Converter.exe` for dev smoke testing).

No Python install is required on the target PC to run the exe.

For company deployment (BeyondTrust / Intune), allowlist the **SHA256 from the GitHub Release page** for the version you deploy. A local build produces a different hash than GitHub Actions.
