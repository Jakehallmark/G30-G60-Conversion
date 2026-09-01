# Copy AnalogoperandTo61850_*.csv from the local URPC Devices folder into firmware/.
# Run after refreshing UR Setup or copying legacy 590/600 tables from an older PC.
#
# Expected after a current URPC 7.30+ sync:
#   g30: 730, 740, 760, 770, 840  (+ 590/600 if sourced from older UR Setup)
#   g60: 840, 850, 860, 870

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent
$Urpc = "C:\ProgramData\GE Power Management\urpc\Devices"

if (-not (Test-Path $Urpc)) {
    throw "URPC Devices folder not found: $Urpc"
}

$g30Revs = @(590, 600, 730, 740, 760, 770, 840)
$g60Revs = @(840, 850, 860, 870)

foreach ($rev in $g30Revs) {
    $src = Join-Path $Urpc "AnalogoperandTo61850_$rev.csv"
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $RepoRoot "firmware\g30\AnalogoperandTo61850_$rev.csv") -Force
        Write-Host "g30  $rev"
    }
}

foreach ($rev in $g60Revs) {
    $src = Join-Path $Urpc "AnalogoperandTo61850_$rev.csv"
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $RepoRoot "firmware\g60\AnalogoperandTo61850_$rev.csv") -Force
        Write-Host "g60  $rev"
    }
}

Write-Host ""
Write-Host "Run: python firmware/verify_csv_pairing.py"
