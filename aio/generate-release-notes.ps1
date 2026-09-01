# Build GitHub Release notes from version.json, built exe hash, and stress test report.
#
#   .\aio\generate-release-notes.ps1
#   .\aio\generate-release-notes.ps1 -OutputPath release/RELEASE_NOTES.md

param(
    [string]$ExePath = "",
    [string]$StressReportPath = "",
    [string]$OutputPath = "",
    [string]$RepoUrl = "https://github.com/Jakehallmark/GE-Multilin-UR-G30-G60-Conversion",
    [switch]$BuiltInCi
)

$ErrorActionPreference = "Stop"
$AioDir = $PSScriptRoot
$RepoRoot = Split-Path $AioDir -Parent

if (-not $ExePath) { $ExePath = Join-Path $RepoRoot "release\G30-to-G60-Converter.exe" }
if (-not $StressReportPath) { $StressReportPath = Join-Path $RepoRoot "docs\STRESS_TEST_REPORT.md" }
if (-not $OutputPath) { $OutputPath = Join-Path $RepoRoot "release\RELEASE_NOTES.md" }

. (Join-Path $AioDir "read-version.ps1")

function Get-MarkdownSection {
    param(
        [string]$Text,
        [string]$Heading
    )
    $pattern = "(?ms)^## $([regex]::Escape($Heading))\r?\n(.*?)(?=^## |\z)"
    $match = [regex]::Match($Text, $pattern)
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return ""
}

function Get-OverallResult {
    param([string]$Text)
    if ($Text -match '\*\*Overall result:\*\*\s*\*\*(.+?)\*\*') {
        return $matches[1].Trim()
    }
    return "See stress test report"
}

$stressBlock = ""
$overall = "N/A"
$reportUrl = "$RepoUrl/blob/main/docs/STRESS_TEST_REPORT.md"

if (Test-Path -LiteralPath $StressReportPath) {
    $report = Get-Content -LiteralPath $StressReportPath -Raw
    $overall = Get-OverallResult $report
    $summary = Get-MarkdownSection -Text $report -Heading "Executive summary"
    $results = Get-MarkdownSection -Text $report -Heading "Results summary"
    $performance = Get-MarkdownSection -Text $report -Heading "Performance"
    $sources = Get-MarkdownSection -Text $report -Heading "Source files"

    $stressBlock = @"
## Reliability validation

**Overall stress test result:** $overall

$summary

### Results

$results

### Performance

$performance

### Site files exercised

$sources

Full report: [$($StressReportPath.Replace('\', '/'))]($reportUrl)
"@
} else {
    $stressBlock = @"
## Reliability validation

Stress test report not found at ``$StressReportPath``. See the repository for the latest validation results.
"@
}

$shaBlock = "SHA256 hash will be available after the release build completes."
$shaHighlight = ""
if (Test-Path -LiteralPath $ExePath) {
    $sha = (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash
    $sizeMb = [math]::Round((Get-Item -LiteralPath $ExePath).Length / 1MB, 1)
    $hashSource = if ($BuiltInCi) {
        "This SHA256 was computed from the **GitHub Actions** build attached to this release. Use this hash for BeyondTrust / Intune — a local `build.ps1` hash will not match."
    } else {
        "For production allowlisting, use the SHA256 on the GitHub Release page (CI-built exe). Local builds produce a different hash."
    }
    $fence = '```'
    $shaHighlight = @"

### SHA256 (BeyondTrust / Intune allowlist)

$fence
$sha
$fence

$hashSource
"@
    $shaBlock = @"
| Property | Value |
|----------|-------|
| File | ``G30-to-G60-Converter.exe`` |
| Size | $sizeMb MB |
| SHA256 | ``$sha`` |
"@
}

$body = @"
# G30 to G60 Converter $AppTag

Standalone Windows GUI for converting GE Multilin UR **G30** relay settings to **G60** format.

## Download

Download **`G30-to-G60-Converter.exe`** from the assets below. No Python install required on the target PC.
$shaHighlight

## IT deployment

$shaBlock

Use the SHA256 hash for BeyondTrust / Intune application allowlisting. **Only the CI-built hash on this release page is valid for deployed copies downloaded from GitHub.**

## What's included

- Wizard-style GUI (URS and XML conversion workflows)
- Post-conversion verification with mismatch reporting
- Bundled G60 base templates for firmware 8.4x–8.7x (XML + paired `.urs`)
- Analogoperand CSV maps from `firmware/` for URS user-display remapping
- System light/dark theme support

$stressBlock

## Documentation

- [README]($RepoUrl#readme)
- [Conversion process]($RepoUrl/blob/main/CONVERSION_PROCESS.md)
- [Stress test report]($reportUrl)

---
*Release $AppDisplayVersion — notes generated automatically by ``aio/generate-release-notes.ps1``.*
"@

$outDir = Split-Path $OutputPath -Parent
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($OutputPath, $body.TrimEnd(), $utf8NoBom)
Write-Host "Wrote release notes: $OutputPath"
