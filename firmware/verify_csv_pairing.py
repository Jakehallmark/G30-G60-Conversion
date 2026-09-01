"""
Validate bundled Analogoperand CSV coverage for each firmware archive and G60 base.

Run from the repository root:

    python firmware/verify_csv_pairing.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from convert_g30_to_g60 import (  # noqa: E402
    build_g30_to_g60_signal_names_via_analogoperands,
    build_signal_operand_tables,
    firmware_version_to_csv_revision,
    parse_xml,
    resolve_analogoperand_csv_detailed,
)

FIRMWARE_DIR = Path(__file__).resolve().parent
BASES_DIR = REPO_ROOT / "bases"

# G30 SFD archives (local, gitignored) → firmware line → expected CSV revision.
G30_FIRMWARE_ARCHIVES = {
    "A09ma590": ("5.9x", 590),
    "A09ma606": ("6.0x", 600),
    "A09ma766": ("7.6x", 760),
    "A09ma844": ("8.4x", 840),
}

# CSVs not shipped with current URPC (7.30+ Devices folder).
G30_CSVS_TO_SOURCE = {
    590: "A09ma590 / FW 5.9x (e.g. HCHPublix Firmware5-9 URS reports version 590)",
    600: "A09ma606 / FW 6.0x (e.g. Publix 1563 Cocoa Beach URS reports version 600)",
}
G60_BASE_VERSIONS = {
    "G60 Base [8.4x].xml": "840",
    "G60 Base [8.5x].xml": "850",
    "G60 Base.xml": "860",
    "G60 Base [8.7x].xml": "870",
}


def _csv_revision(path: Path) -> int:
    suffix = path.stem.rsplit("_", 1)[-1]
    digits = "".join(ch for ch in suffix if ch.isdigit())
    return int(digits) if digits else 0


def _discover_archives(side: str) -> list[str]:
    side_dir = FIRMWARE_DIR / side
    stems: set[str] = set()
    for pattern in ("A09ma*.SFD", "A09ma*.sfd", "A09ma*.000"):
        for path in side_dir.glob(pattern):
            stems.add(path.stem.split(".", 1)[0])
    return sorted(stems)


def _check_archive_coverage(side: str) -> list[str]:
    issues: list[str] = []
    side_dir = FIRMWARE_DIR / side

    for stem in _discover_archives(side):
        suffix = stem.removeprefix("A09ma")
        expected_rev = firmware_version_to_csv_revision(suffix)
        expected_path = side_dir / f"AnalogoperandTo61850_{expected_rev}.csv"

        chosen, _target, exact = resolve_analogoperand_csv_detailed(
            side_dir, preferred_fw=suffix
        )
        if chosen is None:
            issues.append(f"{side}: {stem} -> no Analogoperand CSV found")
            continue

        actual_rev = _csv_revision(chosen)
        if not expected_path.is_file():
            status = "MISSING"
            print(
                f"  [{status}] {stem} (FW {suffix}) needs "
                f"AnalogoperandTo61850_{expected_rev}.csv "
                f"(fallback: {chosen.name})"
            )
            issues.append(
                f"{side}: place AnalogoperandTo61850_{expected_rev}.csv for {stem}"
            )
        elif exact:
            print(
                f"  [OK] {stem} (FW {suffix}) -> {chosen.name} (rev {actual_rev})"
            )
        else:
            print(
                f"  [FALLBACK] {stem} (FW {suffix}) -> {chosen.name} "
                f"(expected rev {expected_rev})"
            )
            issues.append(
                f"{side}: {stem} resolved to {chosen.name}, expected rev {expected_rev}"
            )

    if not _discover_archives(side):
        print(f"  (no local {side} firmware archives — using expected rev table only)")

    return issues


def _check_g60_base_pairs() -> list[str]:
    issues: list[str] = []
    print("\nG60 base template CSV pairing:")
    for base_name, version in G60_BASE_VERSIONS.items():
        base_path = BASES_DIR / base_name
        if not base_path.is_file():
            issues.append(f"Missing base template: {base_name}")
            continue

        g60_csv, _expected, exact = resolve_analogoperand_csv_detailed(
            FIRMWARE_DIR / "g60", preferred_fw=version
        )
        if g60_csv is None:
            issues.append(f"{base_name}: no g60 Analogoperand CSV for version {version}")
            continue

        expected_rev = firmware_version_to_csv_revision(version)
        actual_rev = _csv_revision(g60_csv)
        status = "OK" if exact else "FALLBACK"
        print(f"  [{status}] {base_name} (v{version}) -> {g60_csv.name}")
        if not exact:
            issues.append(f"{base_name}: expected rev {expected_rev}, got {actual_rev}")

    return issues


def _check_join_quality(g30_version: str, g60_version: str) -> list[str]:
    issues: list[str] = []
    g30_csv, _g30_expected, _g30_exact = resolve_analogoperand_csv_detailed(
        FIRMWARE_DIR / "g30", preferred_fw=g30_version
    )
    g60_csv, _g60_expected, _g60_exact = resolve_analogoperand_csv_detailed(
        FIRMWARE_DIR / "g60", preferred_fw=g60_version
    )
    if not g30_csv or not g60_csv:
        issues.append(f"Missing CSV for join G30 {g30_version} + G60 {g60_version}")
        return issues

    g60_xml = BASES_DIR / {
        "840": "G60 Base [8.4x].xml",
        "850": "G60 Base [8.5x].xml",
        "860": "G60 Base.xml",
        "870": "G60 Base [8.7x].xml",
    }.get(g60_version, "G60 Base.xml")
    if not g60_xml.is_file():
        issues.append(f"Missing G60 base for join check: {g60_xml.name}")
        return issues

    g60_root = parse_xml(g60_xml)
    g60_code_to_name, _ = build_signal_operand_tables(g60_root)
    mapping = build_g30_to_g60_signal_names_via_analogoperands(
        g30_csv, g60_csv, g60_code_to_name
    )

    g30_codes = len(
        {
            line.split(",", 2)[0].strip()
            for line in g30_csv.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.startswith("#") and "," in line
        }
    )
    coverage = (len(mapping) / g30_codes * 100) if g30_codes else 0.0
    print(
        f"  Join G30 {g30_csv.name} + G60 {g60_csv.name}: "
        f"{len(mapping):,} mapped codes ({coverage:.1f}% of G30 CSV rows)"
    )

    # SRC P user-display anchors must remap for every supported pair.
    for code, label in (("7168", "SRC1 P"), ("7200", "SRC2 P"), ("7232", "SRC3 P")):
        name = mapping.get(code)
        if not name:
            issues.append(f"Join {g30_version}->{g60_version}: missing remap for {label} ({code})")
        else:
            print(f"    {label} ({code}) -> {name}")

    return issues


def _list_bundled_csvs() -> None:
    print("\nBundled Analogoperand CSVs:")
    for side in ("g30", "g60"):
        side_dir = FIRMWARE_DIR / side
        csvs = sorted(side_dir.glob("AnalogoperandTo61850_*.csv")) if side_dir.is_dir() else []
        if not csvs:
            print(f"  {side}: (none)")
            continue
        revs = ", ".join(str(_csv_revision(path)) for path in csvs)
        print(f"  {side}: {revs}")


def main() -> int:
    print("Analogoperand CSV pairing validation")
    print("=" * 60)

    issues: list[str] = []
    _list_bundled_csvs()

    print("\nG30 firmware archives:")
    issues.extend(_check_archive_coverage("g30"))

    print("\nG60 firmware archives:")
    issues.extend(_check_archive_coverage("g60"))

    issues.extend(_check_g60_base_pairs())

    print("\nG30 CSVs to source (not in current URPC 7.30+ Devices folder):")
    for rev, note in sorted(G30_CSVS_TO_SOURCE.items()):
        path = FIRMWARE_DIR / "g30" / f"AnalogoperandTo61850_{rev}.csv"
        if path.is_file():
            print(f"  [OK] AnalogoperandTo61850_{rev}.csv - {note}")
        else:
            print(f"  [NEEDED] AnalogoperandTo61850_{rev}.csv - {note}")
            issues.append(f"g30: source AnalogoperandTo61850_{rev}.csv ({note})")

    print("\nCross-firmware join checks (typical G30 760 sources):")
    for g60_version in ("840", "850", "860", "870"):
        issues.extend(_check_join_quality("760", g60_version))

    print("\n" + "=" * 60)
    if issues:
        print(f"FAILED: {len(issues)} issue(s)")
        for item in issues:
            print(f"  - {item}")
        return 1

    print("PASSED: all bundled firmware revisions have matching CSVs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
