"""
Post-conversion verification — re-read the written output and compare each
register/setting against the values the converter intended to write.

Tolerates expected firmware differences:

- decimal precision on Number registers
- coded-value display text (``123 (Name)`` vs the same code with a different label)
- bare integer Flex/Enum / assign-VO / FlexLogic END codes vs ``code (display)``
  (the form EnerVista writes for 1.0.8+ URS Flex/Enum parsing)

Unexpected setpoint changes are flagged as mismatches.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from convert_g30_to_g60 import build_lookup, parse_xml
from urs_io import UrsDataRow, parse_urs_file

_CODED_VALUE_RE = re.compile(r"^(\d+)\s+\((.+)\)\s*$", re.DOTALL)
_NUM_PATTERN = re.compile(r"^(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s*(.*)", re.DOTALL)

RegisterKey = tuple[str, str, str, str]


@dataclass(frozen=True)
class SettingMismatch:
    label_id: str
    group: str
    module: str
    item: str
    expected: str
    actual: str
    category: str
    note: str = ""

    @property
    def key(self) -> RegisterKey:
        return (self.label_id, self.group, self.module, self.item)


@dataclass
class VerificationResult:
    ok: bool
    registers_checked: int
    mismatches: list[SettingMismatch] = field(default_factory=list)
    transferred_verified: int = 0
    defaults_verified: int = 0

    def format_summary(self, *, max_mismatches: int = 8) -> str:
        if self.ok:
            parts = [
                f"PASSED — {self.registers_checked:,} register(s) match expected values",
            ]
            if self.transferred_verified:
                parts.append(f"  transferred: {self.transferred_verified:,}")
            if self.defaults_verified:
                parts.append(f"  template defaults: {self.defaults_verified:,}")
            return "\n".join(parts)

        lines = [
            f"FAILED — {len(self.mismatches):,} mismatch(es) "
            f"of {self.registers_checked:,} checked",
        ]
        for mm in self.mismatches[:max_mismatches]:
            lines.append(
                f"  [{mm.category}] {mm.label_id} g={mm.group} m={mm.module} i={mm.item}"
            )
            lines.append(f"    expected: {mm.expected}")
            lines.append(f"    actual:   {mm.actual}")
            if mm.note:
                lines.append(f"    note:     {mm.note}")
        if len(self.mismatches) > max_mismatches:
            lines.append(f"  … and {len(self.mismatches) - max_mismatches} more")
        return "\n".join(lines)

    def format_short(self) -> str:
        if self.ok:
            return f"Verified {self.registers_checked:,} settings — no mismatches"
        return f"{len(self.mismatches):,} setting mismatch(es) — review before import"

    def format_mismatch_details(self) -> str:
        if not self.mismatches:
            return "No mismatches."
        lines: list[str] = []
        for index, mm in enumerate(self.mismatches, start=1):
            lines.append(f"{index}. [{mm.category}] {mm.label_id}")
            lines.append(f"   register: g={mm.group} m={mm.module} i={mm.item}")
            lines.append(f"   expected: {mm.expected}")
            lines.append(f"   actual:   {mm.actual}")
            if mm.note:
                lines.append(f"   note:     {mm.note}")
            lines.append("")
        return "\n".join(lines).rstrip()


def verification_report_path(converted_output: Path) -> Path:
    """Path for a plain-text verification report alongside the converted file."""
    return converted_output.with_name(f"{converted_output.stem}_VR.txt")


def write_verification_report(
    verification: VerificationResult,
    converted_output: Path,
    *,
    source_path: Path | None = None,
    template_label: str = "",
) -> Path:
    """Write mismatch details to a text file next to the converted output."""
    report_path = verification_report_path(converted_output)
    header = [
        "G30 to G60 Converter — verification report",
        "=" * 60,
        f"Converted file : {converted_output.name}",
    ]
    if source_path is not None:
        header.append(f"Source file    : {source_path.name}")
    if template_label:
        header.append(f"Target template: {template_label}")
    header.append("")

    body = [verification.format_summary(max_mismatches=len(verification.mismatches) or 1)]
    if verification.mismatches:
        body.extend(["", "Mismatch details", "-" * 60, verification.format_mismatch_details()])

    report_path.write_text("\n".join(header + body) + "\n", encoding="utf-8")
    return report_path


def split_coded_value(value: str) -> tuple[str, Optional[str]]:
    text = value.strip()
    match = _CODED_VALUE_RE.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    return text, None


def _numbers_equivalent(expected: str, actual: str) -> bool:
    exp_match = _NUM_PATTERN.match(expected.strip())
    act_match = _NUM_PATTERN.match(actual.strip())
    if not exp_match or not act_match:
        return False
    try:
        exp_num = float(exp_match.group(1))
        act_num = float(act_match.group(1))
    except ValueError:
        return False
    if abs(exp_num - act_num) > 1e-9:
        return False
    exp_unit = (exp_match.group(2) or "").strip()
    act_unit = (act_match.group(2) or "").strip()
    if exp_unit and act_unit and exp_unit != act_unit:
        return False
    return True


def values_equivalent(
    expected: str,
    actual: str,
    *,
    setting_type: str = "",
) -> bool:
    """Return True when *actual* matches *expected*, allowing benign formatting drift."""
    exp = expected.strip()
    act = actual.strip()
    if exp == act:
        return True

    exp_code, exp_display = split_coded_value(exp)
    act_code, act_display = split_coded_value(act)
    if exp_display is not None and act_display is not None:
        # Enum / Flex: firmware code must match; display text may differ (VO labels).
        return exp_code == act_code

    # Bare integer vs "code (display)" — URS Flex/Enum, assign-VO, empty END.
    if exp_code.isdigit() and act_code.isdigit() and (
        exp_display is not None or act_display is not None
    ):
        return exp_code == act_code

    if setting_type == "Number" or (
        _NUM_PATTERN.match(exp) and _NUM_PATTERN.match(act)
    ):
        return _numbers_equivalent(exp, act)

    # Bare integer registers (URS packed values, assign-VO codes, etc.)
    if exp_code.isdigit() and act_code.isdigit():
        try:
            return int(exp_code) == int(act_code)
        except ValueError:
            pass

    return False


def _setting_xml_value(setting: ET.Element) -> str:
    stype = setting.get("SettingType", "")
    if stype == "Flex":
        flex_val = setting.get("FlexValue", "")
        display = setting.get("value", "")
        if flex_val and display:
            return f"{flex_val} ({display})"
        return flex_val or display
    if stype == "Enum":
        enum_val = setting.get("EnumValue", "")
        display = setting.get("value", "")
        if enum_val and display:
            return f"{enum_val} ({display})"
        return enum_val or display
    return setting.get("value", "")


def verify_urs_rows(
    expected_rows: list[UrsDataRow],
    output_path: Path,
    *,
    transferred_keys: Optional[set[RegisterKey]] = None,
) -> VerificationResult:
    """Compare in-memory URS rows against the file written to disk."""
    written = parse_urs_file(output_path)
    expected_by_key = {row.key: row for row in expected_rows}
    actual_by_key = written.data_lookup()

    mismatches: list[SettingMismatch] = []
    transferred_verified = 0
    defaults_verified = 0
    transferred_keys = transferred_keys or set()

    if len(expected_by_key) != len(actual_by_key):
        mismatches.append(
            SettingMismatch(
                label_id="(file)",
                group="0",
                module="0",
                item="0",
                expected=str(len(expected_by_key)),
                actual=str(len(actual_by_key)),
                category="structure",
                note="DATA row count",
            )
        )

    for key, exp_row in expected_by_key.items():
        act_row = actual_by_key.get(key)
        category = "transferred" if key in transferred_keys else "template_default"
        if act_row is None:
            mismatches.append(
                SettingMismatch(
                    label_id=key[0],
                    group=key[1],
                    module=key[2],
                    item=key[3],
                    expected=exp_row.value,
                    actual="(missing)",
                    category=category,
                )
            )
            continue

        if category == "transferred":
            transferred_verified += 1
        else:
            defaults_verified += 1

        if not values_equivalent(exp_row.value, act_row.value):
            mismatches.append(
                SettingMismatch(
                    label_id=key[0],
                    group=key[1],
                    module=key[2],
                    item=key[3],
                    expected=exp_row.value,
                    actual=act_row.value,
                    category=category,
                )
            )

    extra_keys = set(actual_by_key) - set(expected_by_key)
    for key in sorted(extra_keys):
        mismatches.append(
            SettingMismatch(
                label_id=key[0],
                group=key[1],
                module=key[2],
                item=key[3],
                expected="(not expected)",
                actual=actual_by_key[key].value,
                category="structure",
                note="unexpected register in output",
            )
        )

    checked = len(expected_by_key)
    structure_ok = not any(m.category == "structure" for m in mismatches)
    return VerificationResult(
        ok=structure_ok and not mismatches,
        registers_checked=checked,
        mismatches=mismatches,
        transferred_verified=transferred_verified,
        defaults_verified=defaults_verified,
    )


def verify_xml_tree(
    expected_root: ET.Element,
    output_path: Path,
    *,
    transferred_keys: Optional[set[tuple[str, str, str, str, str]]] = None,
) -> VerificationResult:
    """Compare in-memory XML settings against the file written to disk."""
    written_root = parse_xml(output_path)
    expected_lookup = build_lookup(expected_root)
    actual_lookup = build_lookup(written_root)
    transferred_keys = transferred_keys or set()

    mismatches: list[SettingMismatch] = []
    transferred_verified = 0
    defaults_verified = 0

    for key, exp_setting in expected_lookup.items():
        act_setting = actual_lookup.get(key)
        category = "transferred" if key in transferred_keys else "template_default"
        stype = exp_setting.get("SettingType", "")
        exp_val = _setting_xml_value(exp_setting)

        if act_setting is None:
            mismatches.append(
                SettingMismatch(
                    label_id=key[0],
                    group=key[1],
                    module=key[2],
                    item=key[3],
                    expected=exp_val,
                    actual="(missing)",
                    category=category,
                )
            )
            continue

        act_val = _setting_xml_value(act_setting)
        if category == "transferred":
            transferred_verified += 1
        else:
            defaults_verified += 1

        if not values_equivalent(exp_val, act_val, setting_type=stype):
            mismatches.append(
                SettingMismatch(
                    label_id=key[0],
                    group=key[1],
                    module=key[2],
                    item=key[3],
                    expected=exp_val,
                    actual=act_val,
                    category=category,
                    note=stype,
                )
            )

    checked = len(expected_lookup)
    return VerificationResult(
        ok=not mismatches,
        registers_checked=checked,
        mismatches=mismatches,
        transferred_verified=transferred_verified,
        defaults_verified=defaults_verified,
    )
