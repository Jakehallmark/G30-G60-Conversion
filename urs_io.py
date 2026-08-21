"""
GE Multilin UR series .urs file I/O.

URS (URPC device export) is a line-oriented text format used by Enervista / UR
communications tools.  Each file contains:

  HEADER,...          device identity and metadata
  URPC_DATA,...       labels, FlexLogic metadata, front-panel strings, etc.
  DATA,...            register settings (labelID, reg#, group, module, item, value)
  END                 end of register block
  [optional sections] FlexGraphFormat, Metering formats, etc.

This module parses and writes .urs files while preserving opaque sections
(URPC_DATA lines, tail blocks) byte-for-byte where possible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Matches "123 (Display Name)" used for Enum and Flex register values.
_CODED_VALUE_RE = re.compile(r"^(\d+)\s+\((.+)\)\s*$", re.DOTALL)

# Post-END sections present on EnerVista device exports (blank addresses = unused).
_DEVICE_FORMAT_TAIL = [
    "[FlexGraphFormat]",
    "Binary - StartAddress=0000000000",
    "Binary - EndAddress=0000000000",
    "[MIFGraphFormat]",
    "Text - StartAddress=0000000000",
    "Text - EndAddress=0000000000",
    "[AlarmsFormat]",
    "Text - StartAddress=0000000000",
    "Text - EndAddress=0000000000",
    "[Metering0Format]",
    "Text - StartAddress=0000000000",
    "Text - EndAddress=0000000000",
    "[Metering1Format]",
    "Text - StartAddress=0000000000",
    "Text - EndAddress=0000000000",
    "[Metering2Format]",
    "Text - StartAddress=0000000000",
    "Text - EndAddress=0000000000",
    "[Metering3Format]",
    "Text - StartAddress=0000000000",
    "Text - EndAddress=0000000000",
    "[Metering4Format]",
    "Text - StartAddress=0000000000",
    "Text - EndAddress=0000000000",
    "[VAL_61850]",
    "Text - StartAddress=0000000000",
    "Text - EndAddress=0000000000",
    "[DEV_SCL_STRUCT]",
    "Text - StartAddress=0000000000",
    "Text - EndAddress=0000000000",
]


@dataclass(frozen=True)
class UrsDataRow:
    """One DATA line in a .urs file."""

    label_id: str
    reg_num: str
    group: str
    module: str
    item: str
    value: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.label_id, self.group, self.module, self.item)

    def to_line(self) -> str:
        return f"DATA,{self.label_id},{self.reg_num},{self.group},{self.module},{self.item},{self.value}"


@dataclass
class UrsFile:
    """Parsed representation of a .urs file."""

    header_fields: list[str]
    urpc_lines: list[str] = field(default_factory=list)
    data_rows: list[UrsDataRow] = field(default_factory=list)
    tail_lines: list[str] = field(default_factory=list)

    @property
    def header_line(self) -> str:
        return ",".join(self.header_fields)

    @property
    def order_code(self) -> str:
        return self.header_fields[3] if len(self.header_fields) > 3 else ""

    @property
    def firmware_version(self) -> str:
        return self.header_fields[4] if len(self.header_fields) > 4 else ""

    def data_lookup(self) -> dict[tuple[str, str, str, str], UrsDataRow]:
        lookup: dict[tuple[str, str, str, str], UrsDataRow] = {}
        for row in self.data_rows:
            lookup.setdefault(row.key, row)
        return lookup


def parse_urs_file(path: Path) -> UrsFile:
    """Read and parse a .urs file.

    EnerVista / UR Setup write .urs files in a single-byte ANSI encoding
    (Latin-1 / Windows-1252), e.g. the angle degree sign is the single byte
    0xB0 in "75\xb0 Lag".  Latin-1 maps every byte 0x00-0xFF to U+0000-U+00FF
    bijectively, so reading (and later writing) with Latin-1 preserves those
    bytes exactly instead of corrupting them the way UTF-8 does.
    """
    text = path.read_text(encoding="latin-1")
    lines = text.splitlines()

    if not lines:
        raise ValueError(f"Empty URS file: {path}")

    header = lines[0]
    if not header.startswith("HEADER,"):
        raise ValueError(f"Expected HEADER line first in {path}")

    result = UrsFile(header_fields=header.split(","))
    past_end = False

    for line in lines[1:]:
        if past_end:
            result.tail_lines.append(line)
            continue

        if line == "END":
            result.tail_lines.append(line)
            past_end = True
            continue

        if line.startswith("DATA,"):
            row = _parse_data_line(line)
            if row is not None:
                result.data_rows.append(row)
            continue

        if line.startswith("URPC_DATA,"):
            result.urpc_lines.append(line)
            continue

        # Lines before END that are not DATA/URPC (shouldn't happen in known files).
        result.urpc_lines.append(line)

    return result


def ensure_device_tail(tail_lines: list[str]) -> list[str]:
    """
    Ensure device exports include the post-END format sections EnerVista expects.

    Blank G60 base templates exported from UR Setup often contain only ``END``;
    full device readbacks also include FlexGraph/Metering format blocks.
    """
    if any(line.startswith("[FlexGraphFormat]") for line in tail_lines):
        return tail_lines

    if not tail_lines:
        return ["END", *_DEVICE_FORMAT_TAIL]

    end_idx = tail_lines.index("END") if "END" in tail_lines else -1
    if end_idx >= 0:
        return tail_lines[: end_idx + 1] + _DEVICE_FORMAT_TAIL

    return ["END", *_DEVICE_FORMAT_TAIL]


def write_urs_file(urs: UrsFile, path: Path) -> None:
    """Write a .urs file using Windows CRLF line endings (required by EnerVista).

    Written in Latin-1 to match EnerVista's single-byte ANSI encoding so that
    extended characters (e.g. the degree sign 0xB0 in angle settings) are
    emitted as the exact bytes EnerVista expects, rather than UTF-8 multibyte
    sequences.
    """
    lines: list[str] = [urs.header_line]
    lines.extend(urs.urpc_lines)
    lines.extend(row.to_line() for row in urs.data_rows)

    tail = ensure_device_tail(urs.tail_lines)
    if tail and tail[0] != "END":
        lines.append("END")
    lines.extend(tail)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("latin-1", errors="replace"))


def _parse_data_line(line: str) -> Optional[UrsDataRow]:
    """Parse a DATA line, splitting on the first six commas only."""
    if not line.startswith("DATA,"):
        return None
    parts = line.split(",", 6)
    if len(parts) < 7:
        return None
    return UrsDataRow(
        label_id=parts[1],
        reg_num=parts[2],
        group=parts[3],
        module=parts[4],
        item=parts[5],
        value=parts[6],
    )


def urs_value_to_setting_attrs(urs_value: str, setting_type: str) -> dict[str, str]:
    """
    Convert a URS register value string into XML Setting attributes.

    URS encodes Enum/Flex values as ``code (display name)`` while Number/Text
    values are stored as plain strings (e.g. ``30 min``, ``1.000 s``).

    FlexLogic assign-VO writes and empty END tokens are stored as a bare
    integer (``3276801``, ``2097152``) with no display name. Those must still
    populate FlexValue so remapping can see the code.
    """
    attrs: dict[str, str] = {"value": urs_value}

    if setting_type in ("Enum", "Flex"):
        match = _CODED_VALUE_RE.match(urs_value)
        if match:
            code, display = match.group(1), match.group(2)
            attrs["value"] = display
            if setting_type == "Enum":
                attrs["EnumValue"] = code
            else:
                attrs["FlexValue"] = code
        else:
            stripped = (urs_value or "").strip()
            if stripped.isdigit():
                if setting_type == "Enum":
                    attrs["EnumValue"] = stripped
                else:
                    attrs["FlexValue"] = stripped

    return attrs


def setting_attrs_to_urs_value(attrs: dict[str, str], setting_type: str) -> str:
    """Convert XML Setting attributes back to a URS register value string."""
    if setting_type == "Enum":
        enum_val = attrs.get("EnumValue", "")
        display = attrs.get("value", "")
        if enum_val != "":
            return f"{enum_val} ({display})"
    elif setting_type == "Flex":
        flex_val = attrs.get("FlexValue", "")
        display = attrs.get("value", "")
        if flex_val != "":
            return f"{flex_val} ({display})"

    return attrs.get("value", "")


def make_setting_element(
    label_id: str,
    group: str,
    module: str,
    item: str,
    bit: str,
    setting_type: str,
    urs_value: str,
) -> "ET.Element":
    """Build a minimal Setting element from a URS value for transfer logic."""
    import xml.etree.ElementTree as ET

    attrs = urs_value_to_setting_attrs(urs_value, setting_type)
    el = ET.Element(
        "Setting",
        labelID=label_id,
        group=group,
        module=module,
        item=item,
        bit=bit,
        SettingType=setting_type,
    )
    for key, val in attrs.items():
        el.set(key, val)
    return el


def update_header_for_output(
    header_fields: list[str],
    order_code: str,
    firmware_version: str,
    *,
    timestamp: Optional[datetime] = None,
) -> list[str]:
    """
    Return a copy of HEADER fields updated for a converted output file.

    Preserves the template checksum/token (field 8) — EnerVista rejects files
    with a missing or empty token.
    """
    fields = list(header_fields)
    while len(fields) < 8:
        fields.append("")

    checksum = fields[7]
    fields[3] = order_code
    fields[4] = firmware_version

    ts = timestamp or datetime.now()
    fields[6] = ts.strftime("%d/%m/%Y %H:%M:%S")
    fields[7] = checksum

    return fields


def _normalize_device_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def find_companion_xml(urs_path: Path) -> Optional[Path]:
    """Return a G30 settings XML for a .urs export, if available.

    Checks, in order:
      1. Sibling ``<stem>.xml`` next to the .urs
      2. EnerVista Offline ``TargetSettingFile.xml`` in a matching folder
    """
    xml_path = urs_path.with_suffix(".xml")
    if xml_path.exists():
        return xml_path

    offline_root = Path(r"C:\ProgramData\GE Power Management\urpc\Offline")
    if not offline_root.is_dir():
        return None

    stem_key = _normalize_device_key(urs_path.stem)
    best: Optional[Path] = None
    best_score = 0
    for folder in offline_root.iterdir():
        if not folder.is_dir():
            continue
        candidate = folder / "TargetSettingFile.xml"
        if not candidate.is_file():
            continue
        folder_key = _normalize_device_key(folder.name)
        if folder_key == stem_key:
            return candidate
        if stem_key in folder_key or folder_key in stem_key:
            score = min(len(stem_key), len(folder_key))
            if score > best_score:
                best_score = score
                best = candidate
    return best


def paired_urs_path(xml_path: Path) -> Optional[Path]:
    """Return the .urs template that pairs with a G60 Base*.xml file."""
    urs_path = xml_path.with_suffix(".urs")
    return urs_path if urs_path.is_file() else None


def resolve_urs_template(
    xml_path: Path,
    base_dir: Path,
    explicit_urs: Optional[Path] = None,
) -> Path:
    """
    Resolve the G60 template .urs that matches a G60 Base*.xml template.

    When explicit_urs is omitted, looks for a .urs file with the same stem as
    xml_path, then falls back to bases/G60 Base.urs.
    """
    if explicit_urs is not None:
        if not explicit_urs.exists():
            raise FileNotFoundError(f"G60 URS template not found: {explicit_urs}")
        return explicit_urs

    paired = paired_urs_path(xml_path)
    if paired is not None:
        return paired

    default = base_dir / "G60 Base.urs"
    if default.is_file():
        return default

    raise FileNotFoundError(
        f"No paired .urs found for {xml_path.name}. Expected {xml_path.with_suffix('.urs')} "
        f"or {default}."
    )


def compare_urs_structure(original: UrsFile, rewritten: UrsFile) -> list[str]:
    """Return human-readable differences between two parsed URS files."""
    issues: list[str] = []

    if len(original.urpc_lines) != len(rewritten.urpc_lines):
        issues.append(
            f"URPC line count: {len(original.urpc_lines)} vs {len(rewritten.urpc_lines)}"
        )

    if len(original.data_rows) != len(rewritten.data_rows):
        issues.append(
            f"DATA row count: {len(original.data_rows)} vs {len(rewritten.data_rows)}"
        )

    orig_by_key = original.data_lookup()
    new_by_key = rewritten.data_lookup()
    if set(orig_by_key) != set(new_by_key):
        missing = set(orig_by_key) - set(new_by_key)
        extra = set(new_by_key) - set(orig_by_key)
        if missing:
            issues.append(f"Missing keys after rewrite: {len(missing)}")
        if extra:
            issues.append(f"Extra keys after rewrite: {len(extra)}")

    value_mismatches = 0
    for key, orig_row in orig_by_key.items():
        new_row = new_by_key.get(key)
        if new_row and orig_row.value != new_row.value:
            value_mismatches += 1
    if value_mismatches:
        issues.append(f"Value mismatches: {value_mismatches}")

    return issues
