# Bump version, commit all pending changes, tag, push to GitHub, and build the exe.
#
#   .\aio\release.ps1                     # bump patch, commit everything, push tag, build
#   .\aio\release.ps1 -Version 1.1.0      # explicit semver
#   .\aio\release.ps1 -Bump minor         # bump minor instead of patch
#   .\aio\release.ps1 -SkipPush           # commit + tag + build locally only
#   .\aio\release.ps1 -SkipBuild          # commit/tag/push only (CI builds the exe)
#
# Stages all tracked/untracked files (respecting .gitignore) together with the
# version bump — not just aio/version.json.

param(
    [string]$Version,
    [ValidateSet("major", "minor", "patch")]
    [string]$Bump = "patch",
    [string]$Message,
    [switch]$SkipPush,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$AioDir = $PSScriptRoot
$RepoRoot = Split-Path $AioDir -Parent
$VersionFile = Join-Path $AioDir "version.json"
$BuildScript = Join-Path $AioDir "build.ps1"

function Get-LatestSemverTag {
    param([string]$RepoPath)
    Push-Location $RepoPath
    try {
        $tags = git tag -l "v*.*.*" | ForEach-Object { $_ -replace "^v", "" }
        $valid = $tags | Where-Object { $_ -match "^\d+\.\d+\.\d+$" } | Sort-Object {
            $parts = $_.Split(".")
            [version]"$($parts[0]).$($parts[1]).$($parts[2])"
        }
        return $valid | Select-Object -Last 1
    } finally {
        Pop-Location
    }
}

function Bump-Semver {
    param(
        [string]$Current,
        [string]$Kind
    )
    $parts = $Current.Split(".")
    if ($parts.Count -lt 3) {
        throw "Invalid semver: $Current"
    }
    [int]$major = $parts[0]
    [int]$minor = $parts[1]
    [int]$patch = $parts[2]
    switch ($Kind) {
        "major" { return "{0}.0.0" -f ($major + 1) }
        "minor" { return "{0}.{1}.0" -f $major, ($minor + 1) }
        default { return "{0}.{1}.{2}" -f $major, $minor, ($patch + 1) }
    }
}

if (-not (Test-Path -LiteralPath $VersionFile)) {
    throw "Missing $VersionFile"
}

$current = Get-Content -LiteralPath $VersionFile -Raw | ConvertFrom-Json
$previousVersion = [string]$current.version
$previousBuild = [int]$current.build

if ($Version) {
    if ($Version -notmatch "^\d+\.\d+\.\d+$") {
        throw "Version must be semver like 1.0.4, got: $Version"
    }
    $newVersion = $Version
} else {
    $tagBase = Get-LatestSemverTag $RepoRoot
    if (-not $tagBase) { $tagBase = "0.0.0" }
    $fromTag = Bump-Semver -Current $tagBase -Kind $Bump
    if ([version]$previousVersion -gt [version]$fromTag) {
        $newVersion = $previousVersion
    } else {
        $newVersion = $fromTag
    }
}

$newBuild = if ($newVersion -ne $previousVersion) {
    1
} else {
    $tagExists = git -C $RepoRoot tag -l "v$newVersion"
    if ($tagExists) { $previousBuild + 1 } else { [Math]::Max($previousBuild, 1) }
}

$versionPayload = [ordered]@{
    version = $newVersion
    build   = $newBuild
}
# Windows PowerShell's -Encoding utf8 writes a BOM; Python json.load rejects it.
$json = ($versionPayload | ConvertTo-Json) + "`n"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($VersionFile, $json, $utf8NoBom)

. (Join-Path $AioDir "read-version.ps1")

Write-Host "Release version: $AppDisplayVersion"
Write-Host "Git tag:         $AppTag"

$pending = git -C $RepoRoot status --porcelain
if ($pending) {
    Write-Host ""
    Write-Host "Staging pending changes for release commit:"
    Write-Host $pending
    git -C $RepoRoot add -A
} else {
    Write-Host ""
    Write-Host "No pending file changes besides version.json."
    git -C $RepoRoot add $VersionFile
}

$staged = @(git -C $RepoRoot diff --cached --name-only)
if ($staged.Count -eq 0) {
    throw "Nothing staged to commit."
}
Write-Host ""
Write-Host "Files in release commit:"
$staged | ForEach-Object { Write-Host "  $_" }

$commitMessage = if ($Message) { $Message } else { "Release $AppTag (build $AppBuild)" }
git -C $RepoRoot commit -m $commitMessage

git -C $RepoRoot tag -a $AppTag -m "Release $AppDisplayVersion"

if (-not $SkipBuild) {
    & $BuildScript
} else {
    Write-Host "Skipped local build (-SkipBuild). GitHub Actions will build on tag push."
}

if ($SkipPush) {
    Write-Host ""
    Write-Host "Skipped push (-SkipPush)."
    Write-Host "When ready: git push origin main && git push origin $AppTag"
    exit 0
}

Write-Host ""
Write-Host "Pushing commit and tag..."
git -C $RepoRoot push origin HEAD
git -C $RepoRoot push origin $AppTag

Write-Host ""
Write-Host "Done."
Write-Host "  Tag:       $AppTag"
Write-Host "  Exe:       $(Join-Path $RepoRoot 'release\G30-to-G60-Converter.exe')"
Write-Host "  GitHub CI will build the exe, compute SHA256 from that CI build,"
Write-Host "  generate release notes, and publish the Release for $AppTag."
Write-Host "  Use the SHA256 on the GitHub Release page for IT allowlisting (not a local build hash)."
