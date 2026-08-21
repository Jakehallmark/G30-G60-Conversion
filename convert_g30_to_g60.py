"""
G30 to G60 relay settings converter for GE Multilin UR series.

Produces:
  1. Output XML  - G60 structure with G30 values transferred in
  2. HTML report - same base name as output XML, with _OR suffix

Matching key: (labelID, group, module, item, bit)

Base Template:
  Uses bases/G60 Base.xml (default) under the same folder as this script.

Output naming:
  - Reads deviceName and version from the G30 source file.
  - Reads deviceName from the G60 template (carries the G60 model identifier).
  - Replaces the G30 model identifier in the G30 deviceName with the G60 model
    identifier, then applies UR Setup's title-casing convention to produce the
    output filename (e.g. "publix g60_208v4000a[86]" -> "Publix G60_208V4000A[86]").
  - The output XML's deviceName attribute is updated to match.
  - version and orderCode in the output XML are always preserved from the G60 template.

Usage:
  python convert_g30_to_g60.py [g30_input] [output_dir]
  (g30_input required; output_dir defaults to ./Converted)
"""

from __future__ import annotations

import html as html_lib
import re
import xml.etree.ElementTree as ET
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from verify_conversion import VerificationResult


# ΓöÇΓöÇ Data structures ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

@dataclass
class TransferredRecord:
    path: str
    label_id: str
    group: str
    module: str
    item: str
    bit: str
    g60_name: str
    g30_name: str
    setting_type: str
    g30_value: str
    g60_template_value: str
    range_warning: Optional[str] = None
    original_g30_value: Optional[str] = None
    adjustment_note: Optional[str] = None

    @property
    def value_changed(self) -> bool:
        return self.g30_value != self.g60_template_value

    @property
    def name_changed(self) -> bool:
        return self.g30_name != self.g60_name


@dataclass
class DroppedRecord:
    path: str
    label_id: str
    group: str
    module: str
    name: str
    setting_type: str
    value: str


@dataclass
class G60OnlyRecord:
    path: str
    label_id: str
    group: str
    module: str
    name: str
    setting_type: str
    default_value: str


# ΓöÇΓöÇ XML helpers ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

def get_device_name(xml_root: ET.Element) -> str:
    """Extract the deviceName attribute from the XML root element."""
    return xml_root.get("deviceName", "").lower()


def base_templates_dir(app_dir: Path) -> Path:
    """Return the directory containing G60 Base*.xml templates."""
    nested = app_dir / "bases"
    if nested.is_dir() and any(nested.glob("G60 Base*.xml")):
        return nested
    return app_dir


def select_base_template(_g30_path: Path, base_dir: Path) -> Path:
    """Return the default G60 base template (bases/G60 Base.xml)."""
    template_dir = base_templates_dir(base_dir)
    base_standard = template_dir / "G60 Base.xml"
    if base_standard.exists():
        return base_standard
    raise FileNotFoundError(
        f"G60 base template not found: {base_standard}"
    )


def parse_xml(path: Path) -> ET.Element:
    with open(path, "rb") as f:
        raw = f.read()

    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        try:
            return ET.fromstring(raw.decode("utf-16-le"))
        except (UnicodeDecodeError, ET.ParseError):
            raise


def build_lookup(root: ET.Element) -> dict:
    """Return {(labelID, group, module, item, bit): Element} for every Setting."""
    lookup = {}
    for setting in root.iter("Setting"):
        key = (
            setting.get("labelID", ""),
            setting.get("group", "0"),
            setting.get("module", "0"),
            setting.get("item", "0"),
            setting.get("bit", "0"),
        )
        if key not in lookup:
            lookup[key] = setting
    return lookup


def ur_title_case(device_name: str) -> str:
    """
    Reproduce the UR Setup file-naming convention from a lowercase deviceName.
    Rules (derived from observed filenames vs XML attributes):
      1. Capitalize the first letter of each space-delimited token.
      2. Capitalize any letter that immediately follows a digit (handles V, A, W units).
    Example: 'publix g60_208v4000a[86]' -> 'Publix G60_208V4000A[86]'
    """
    result = []
    first_alpha = False
    prev_digit = False
    for ch in device_name:
        if ch == " ":
            first_alpha = False
            prev_digit = False
            result.append(ch)
        elif ch.isalpha():
            if not first_alpha or prev_digit:
                result.append(ch.upper())
                first_alpha = True
            else:
                result.append(ch)
            prev_digit = False
        elif ch.isdigit():
            result.append(ch)
            prev_digit = True
        else:
            result.append(ch)
            prev_digit = False
    return "".join(result)


def derive_output_file_stem(device_name: str, firmware_version: str) -> str:
    """Build the output file base name, including firmware to avoid cross-version overwrites."""
    fw = firmware_version.strip()
    if fw:
        return f"{device_name} FW{fw}"
    return device_name


def _is_spec_token(token: str) -> bool:
    t = token.lower()
    return bool(re.fullmatch(r"\d+v", t) or re.fullmatch(r"\d+a", t))


def _is_firmware_token(token: str) -> bool:
    t = token.lower()
    if t.startswith("firmware"):
        return True
    if re.fullmatch(r"\d+\.\d+", t):
        return True
    if re.fullmatch(r"\d+-\d+", t):
        return True
    return False


def _is_site_descriptor_token(token: str) -> bool:
    return not _is_firmware_token(token) and not _is_spec_token(token)


def derive_g30_identity_from_urs_stem(stem: str) -> str:
    """Build a lowercase deviceName-like string from a .urs export filename.

    EnerVista filenames often omit the underscore form used in XML deviceName
    attributes (e.g. ``Publix 1563 Cocoa Beach 6.06 208v 3000A``).  When no
    underscore is present, keep site/location tokens (store number, city, etc.),
    extract voltage/amperage, and synthesize the ``site _208v3000a`` form so URS
    and XML outputs share the same naming scheme.
    """
    name = stem.strip().lower()
    name = re.sub(r"\s+\d{1,2}-\d{1,2}-\d{2,4}.*$", "", name)
    name = re.sub(r"\s*\([^)]*\)", "", name).strip()

    if "_" in name:
        return name

    tokens = name.split()
    if not tokens:
        return name

    first_word = tokens[0]
    descriptor = [t for t in tokens[1:] if _is_site_descriptor_token(t)]

    volt = re.search(r"(\d+)\s*v", name)
    amp = re.search(r"(\d+)\s*a", name)
    spec_parts: list[str] = []
    if volt:
        spec_parts.append(f"{volt.group(1)}v")
    if amp:
        spec_parts.append(f"{amp.group(1)}a")
    spec_suffix = f"_{''.join(spec_parts)}" if spec_parts else ""

    if descriptor:
        return f"{first_word} {' '.join(descriptor)}{spec_suffix}"
    if spec_suffix:
        return f"{first_word}{spec_suffix}"
    return name


def derive_output_device_name(g30_device_name: str, g60_order_code: str) -> str:
    """
    Build the output deviceName from the G30 device identity and the G60 model.

    Approach:
      - Site prefix  = first word of G30 deviceName       (e.g. 'publix')
      - Descriptor   = remaining tokens before the specs
                       underscore, excluding firmware/spec tokens
                       (e.g. '1563 cocoa beach')
      - G60 model    = first segment of G60 orderCode,
                       lowercased                          (e.g. 'G60' -> 'g60')
      - Specs suffix = everything from the first '_' in
                       the G30 deviceName onwards          (e.g. '_208v4000a[86]')
      - Result       = '{prefix} {descriptor} {model}{suffix}'

    This is robust to any G60 template because it reads the model from the
    orderCode rather than from the template's deviceName.

    Example:
      g30_device_name = 'publix firmware7-6_208v4000a[86]'
      g60_order_code  = 'G60-V00-HCL-F8L-H6P-M8L-P5A-UXX-WXX'
      result          = 'publix g60_208v4000a[86]'
    """
    g60_model = g60_order_code.split("-")[0].lower()
    underscore_idx = g30_device_name.find("_")
    suffix = g30_device_name[underscore_idx:] if underscore_idx != -1 else ""
    prefix_part = (
        g30_device_name[:underscore_idx].strip()
        if underscore_idx != -1
        else g30_device_name.strip()
    )

    tokens = prefix_part.split()
    if not tokens:
        return f"{g60_model}{suffix}".strip()

    first_word = tokens[0]
    middle_tokens = [t for t in tokens[1:] if _is_site_descriptor_token(t)]
    middle = " ".join(middle_tokens)

    if middle:
        return f"{first_word} {middle} {g60_model}{suffix}"
    return f"{first_word} {g60_model}{suffix}"


def validate_g30_settings_xml(path: Path) -> ET.Element:
    """Parse and validate a G30 UR Setup settings export.

    Raises:
        ValueError: If the file is not a usable G30 settings export.
    """
    try:
        root = parse_xml(path)
    except ET.ParseError as exc:
        raise ValueError(f"Not a valid XML file: {path.name}") from exc

    if root.tag != "URDevice":
        raise ValueError(
            f"Expected a UR Setup device export (<URDevice> root), not <{root.tag}>."
        )

    order_code = (root.get("orderCode") or "").upper()
    if order_code.startswith("G60"):
        raise ValueError(
            "This file appears to be a G60 settings export. Select a G30 source file."
        )
    if order_code and not order_code.startswith("G30"):
        raise ValueError(
            f"Unexpected relay model ({root.get('orderCode')}). Expected a G30 export."
        )

    if next(root.iter("Setting"), None) is None:
        raise ValueError(
            "No settings found in this XML file. It may be empty or not a settings export."
        )

    return root


def build_path_map(root: ET.Element) -> dict:
    """Return {id(element): breadcrumb_path} for every element in the tree."""
    paths = {}

    def walk(el, crumb):
        paths[id(el)] = crumb
        tag = el.tag
        name = el.get("screenName", tag)
        child_crumb = f"{crumb} > {name}" if crumb else name
        for child in el:
            walk(child, child_crumb)

    for child in root:
        walk(child, "")

    return paths


def parse_number_value(raw: str) -> Optional[float]:
    try:
        match = _NUM_PATTERN.match(raw.strip())
        if not match:
            return None
        return float(match.group(1))
    except (ValueError, AttributeError):
        return None


def check_range(setting: ET.Element, g30_value_str: str) -> Optional[str]:
    val = parse_number_value(g30_value_str or "")
    if val is None:
        return None

    lo = setting.get("MinValue")
    hi = setting.get("MaxValue")
    try:
        if lo is not None and val < float(lo):
            return f"{val} < G60 min {lo}"
        if hi is not None and val > float(hi):
            return f"{val} > G60 max {hi}"
    except ValueError:
        pass
    return None


def maybe_adjust_legacy_number_value(g60_el: ET.Element, g30_value: str) -> tuple[Optional[str], Optional[str]]:
    """Handle special-case behavior for legacy Number settings.

    For `UR_DATA_IEC_POWER_FACTOR_DEFAULT_THRESHOLD` it is best that the G60
    template default be preserved rather than transferring
    (or scaling) the legacy G30 value. Return the G60 template value as the
    adjusted value so the transfer logic writes the template default.
    """
    label = g60_el.get("labelID", "")
    if label == "UR_DATA_IEC_POWER_FACTOR_DEFAULT_THRESHOLD":
        # Preserve the template default value rather than transferring legacy raw
        return g60_el.get("value", ""), "Preserved G60 template default"
    return None, None


_NUM_PATTERN = re.compile(r"^(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s*(.*)", re.DOTALL)
_FLEX_OPERAND_TABLE_INDEXES = ("10012", "10013")
_SIGNAL_OPERAND_TABLE_INDEX = "10013"
_USER_DISPLAY_ITEMS_LABEL = "UR_DATA_USER_DISPLAY_X_DISPLAYED_ITEMS"
_G60_USER_DISPLAY_CODE_OFFSET = 262144  # G60 user-display item codes = signal enum code + 0x40000
_G30_TO_G60_FLEX_OFFSET = 391680  # Shifts G30 VO/contact read codes to G60 (e.g. 1537 -> 393217)
_G30_ASSIGN_VO_BASE = 12800
_G60_ASSIGN_VO_BASE = 3276800  # 12800 * 256; G60 assign-VO codes are 0x320000 + VO number
_ASSIGN_VO_COUNT = 256
_ASSIGN_VO_SUFFIX = re.compile(r"\(VO(\d+)\)\s*$")
_HW_ADDR_SUFFIX = re.compile(r"\(([^)]+)\)\s*$")
_ON_WORD = re.compile(r"\bOn\b")
_OFF_WORD = re.compile(r"\bOff\b")
_FLEXLOGIC_PRIMITIVE = re.compile(r"^(END|NOT|XOR|AND|OR|NAND|NOR|TIMER)\b", re.IGNORECASE)
_FLEXLOGIC_END_PACKED = 8192  # (0x20 << 8)
_FLEXLOGIC_END_WIDE = 2097152  # (0x20 << 16)

# IPv4 settings are stored as Number with MaxValue ~2^32-1. XML uses width-3
# space-padded dotted-quads ('127.  0.  0.  1'); URS uses packed uint32.
# Feeding them through reformat_number_value treats the first octet as a float
# and grafts the rest of the template as a "unit", corrupting the address.
_IPV4_LABEL_MARKERS = (
    "IPV4",
    "_IP_ADDRESS",
    "IP_SUBNET",
    "SUBNET_MASK",
    "NETMASK",
    "_GATEWAY",
    "RT_X_DESTINATION",
    "RT_X_NETMASK",
    "RT_X_GATEWAY",
    "DNP_CLIENT_ADDRESS",
    "IEC_CLIENT_ADDRESS",
)
_IPV4_LABEL_EXCLUSIONS = ("_PORT", "UDP_PORT", "TCP_PORT", "HTTP_PORT")
_DOTTED_IPV4_RE = re.compile(
    r"^\s*(\d{1,3})\s*\.\s*(\d{1,3})\s*\.\s*(\d{1,3})\s*\.\s*(\d{1,3})\s*$"
)
_PACKED_IPV4_RE = re.compile(r"^\s*(\d{1,10})\s*$")


def build_flex_operand_map(root: ET.Element) -> dict[str, str]:
    """Map flex operand display names to G60 FlexValue codes from firmware operand tables.

    UR Setup stores operand dictionaries in large EnumType Items attributes:
      10012 ΓÇö logic, contacts, virtual outputs, protection element outputs
      10013 ΓÇö measurement/signal sources (SRC1 Ia RMS, SRC1 P, etc.)

    The blank G60 template rarely references configured operands in its Setting
    elements, so LED assignments, oscillography sources, and similar Flex picks must
    be resolved through these tables rather than the settings-based flex map alone.
    """
    operand_map: dict[str, str] = {}
    seen_indexes: set[str] = set()

    for table_index in _FLEX_OPERAND_TABLE_INDEXES:
        for el in root.iter("EnumType"):
            if el.get("FormatIndex") != table_index:
                continue
            items = el.get("Items", "")
            if items:
                _parse_flex_operand_items(items, operand_map)
                seen_indexes.add(table_index)
                break

    # Fallback when FormatIndex values differ between firmware exports.
    for el in root.iter("EnumType"):
        table_index = el.get("FormatIndex", "")
        if table_index in seen_indexes:
            continue
        items = el.get("Items", "")
        if _is_logic_operand_table(items) or _is_signal_operand_table(items):
            _parse_flex_operand_items(items, operand_map)
            if table_index:
                seen_indexes.add(table_index)

    return operand_map


def _parse_flex_operand_items(items: str, operand_map: dict[str, str]) -> None:
    for entry in items.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        code, sep, name = entry.partition(" ")
        if not sep or not code.isdigit() or not name:
            continue
        name = name.strip()
        if name not in operand_map:
            operand_map[name] = code


def build_signal_operand_tables(root: ET.Element) -> tuple[dict[str, str], dict[str, str]]:
    """Return (code_to_name, name_to_code) maps from the measurement/signal operand table (10013).

    G30 user-display Item values store these enum codes directly. G60 stores the same
    signal at enum_code + _G60_USER_DISPLAY_CODE_OFFSET.
    """
    code_to_name: dict[str, str] = {}
    name_to_code: dict[str, str] = {}

    def ingest(items: str) -> None:
        for entry in items.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            code, sep, name = entry.partition(" ")
            if not sep or not code.isdigit() or not name:
                continue
            name = name.strip()
            code_to_name[code] = name
            if name not in name_to_code:
                name_to_code[name] = code

    for el in root.iter("EnumType"):
        if el.get("FormatIndex") == _SIGNAL_OPERAND_TABLE_INDEX:
            items = el.get("Items", "")
            if items:
                ingest(items)
                return code_to_name, name_to_code

    for el in root.iter("EnumType"):
        items = el.get("Items", "")
        if _is_signal_operand_table(items):
            ingest(items)
            break

    return code_to_name, name_to_code


def remap_user_display_item(
    g30_value: str,
    g30_code_to_name: dict[str, str],
    g60_name_to_code: dict[str, str],
) -> tuple[Optional[str], Optional[str]]:
    """Translate a G30 user-display Item code to its G60 equivalent.

    Returns (g60_value, adjustment_note). When the first element is None the caller
    should keep the G60 template default.
    """
    g30_value = (g30_value or "").strip()
    if not g30_value or g30_value == "0":
        return "0", None

    if not g30_code_to_name or not g60_name_to_code:
        return None, "User display signal table unavailable; kept G60 template default"

    signal_name = g30_code_to_name.get(g30_value)
    if not signal_name:
        return None, f"Unknown G30 user display signal code {g30_value}; kept G60 template default"

    g60_code = g60_name_to_code.get(signal_name)
    if not g60_code:
        return None, (
            f"Signal unavailable on G60 ({signal_name.strip()}); kept G60 template default"
        )

    g60_display = str(int(g60_code) + _G60_USER_DISPLAY_CODE_OFFSET)
    return g60_display, (
        f"User display item: {signal_name.strip()} ({g30_value} -> {g60_display})"
    )


_ANALOGOPERAND_IEC_MAG_RE = re.compile(r"\.instMag\.f$|\.mag\.f$|\.f$")
_CODED_URS_VALUE_RE = re.compile(r"^(\d+)\s+\((.+)\)\s*$", re.DOTALL)


def normalize_iec_operand_stem(iec_path: str) -> str:
    """Collapse IEC61850 FlexAnalog paths to a join key (drop .instMag.f / .mag.f)."""
    stem = (iec_path or "").strip().replace(" ", "")
    return _ANALOGOPERAND_IEC_MAG_RE.sub("", stem)


def parse_analogoperand_to_61850_csv(path: Path) -> dict[str, str]:
    """Parse AnalogoperandTo61850_*.csv → {decimal_code: iec_path}.

    GE ships these under URPC Devices/ as ``HEX,Display Name,IEC.Path``.
    Row 0 / FFFFFFFE sentinels and blank lines are skipped. Duplicate hex codes
    keep the last non-empty path.
    """
    code_to_path: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        hex_code, _name, iec_path = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not hex_code or not iec_path:
            continue
        if hex_code.upper() == "FFFFFFFE":
            continue
        try:
            code = str(int(hex_code, 16))
        except ValueError:
            continue
        code_to_path[code] = iec_path
    return code_to_path


def build_g30_to_g60_signal_names_via_analogoperands(
    g30_csv: Path,
    g60_csv: Path,
    g60_code_to_name: dict[str, str],
) -> dict[str, str]:
    """Map G30 user-display signal codes → G60 EnumType-10013 display names.

    Joins GE ``AnalogoperandTo61850`` CSVs on normalized IEC61850 path stems,
    then resolves the G60 decimal code through the G60 Base signal table. Codes
    that join to a G60 operand absent from table 10013 are omitted (those are
    not valid user-display items on the target relay).
    """
    g30_code_to_path = parse_analogoperand_to_61850_csv(g30_csv)
    g60_code_to_path = parse_analogoperand_to_61850_csv(g60_csv)

    stem_to_g60_code: dict[str, str] = {}
    for code, iec_path in g60_code_to_path.items():
        stem = normalize_iec_operand_stem(iec_path)
        if not stem:
            continue
        # First code wins for a stem (UR lists are stable; avoid thrashing).
        stem_to_g60_code.setdefault(stem, code)

    result: dict[str, str] = {}
    for g30_code, iec_path in g30_code_to_path.items():
        stem = normalize_iec_operand_stem(iec_path)
        g60_code = stem_to_g60_code.get(stem)
        if not g60_code:
            continue
        g60_name = g60_code_to_name.get(g60_code)
        if not g60_name:
            continue
        result[g30_code] = g60_name
    return result


def build_signal_code_map_from_urs_annotations(
    coded_values: list[tuple[str, str]],
    g60_name_to_code: dict[str, str],
) -> dict[str, str]:
    """Build code→name from URS ``N (Name)`` annotations that match G60 signal names.

    Used as a sparse supplement when a G30 code appears on DCMA / oscillography
    (etc.) with a display name but is missing from the Analogoperand join.
    """
    result: dict[str, str] = {}
    for code, name in coded_values:
        name = (name or "").strip()
        if not code or not name:
            continue
        if name in g60_name_to_code:
            result.setdefault(code, name)
    return result


def parse_coded_urs_display(urs_value: str) -> tuple[str, str]:
    """Split ``123 (Display)`` into (code, display); otherwise ("", value)."""
    match = _CODED_URS_VALUE_RE.match((urs_value or "").strip())
    if match:
        return match.group(1), match.group(2)
    return "", urs_value or ""


def _analogoperand_csv_revision(path: Path) -> int:
    """Extract the numeric revision from ``AnalogoperandTo61850_<rev>.csv``."""
    suffix = path.stem.rsplit("_", 1)[-1]
    digits = "".join(ch for ch in suffix if ch.isdigit())
    return int(digits) if digits else 0


# SFD build suffixes that differ from the URDevice ``version`` field.
_SFD_SUFFIX_TO_CSV_REV: dict[str, int] = {
    "606": 600,  # A09ma606 archive is FW 6.0x; projects report version 600
}


def firmware_version_to_csv_revision(version: str) -> int:
    """Map a UR firmware version or SFD build suffix to an Analogoperand CSV rev.

    G30 archives in ``firmware/g30/`` map to firmware lines as:
      A09ma590 → 5.9x (590), A09ma606 → 6.0x (600), A09ma766 → 7.6x (760),
      A09ma844 → 8.4x (840).

    5.x/6.x builds use the URDevice version field (590, 600). The ``A09ma606``
    SFD package is 6.06 but projects report ``version="600"``. 7.x/8.x builds
    floor to the line revision (766 → 760, 844 → 840).
    """
    digits = "".join(ch for ch in (version or "") if ch.isdigit())
    if not digits:
        return 0
    key = digits[:3] if len(digits) >= 3 else digits
    mapped = _SFD_SUFFIX_TO_CSV_REV.get(key)
    if mapped is not None:
        return mapped
    n = int(key)
    if n < 700:
        return n
    return (n // 10) * 10


def resolve_analogoperand_csv(
    firmware_side_dir: Path, preferred_fw: str = ""
) -> Optional[Path]:
    """Pick AnalogoperandTo61850_*.csv under firmware/g30 or firmware/g60."""
    path, _expected, _exact = resolve_analogoperand_csv_detailed(firmware_side_dir, preferred_fw)
    return path


def resolve_analogoperand_csv_detailed(
    firmware_side_dir: Path, preferred_fw: str = ""
) -> tuple[Optional[Path], int, bool]:
    """Return (csv_path, expected_revision, exact_match)."""
    if not firmware_side_dir.is_dir():
        return None, 0, False
    candidates = sorted(firmware_side_dir.glob("AnalogoperandTo61850_*.csv"))
    if not candidates:
        return None, 0, False

    by_revision: dict[int, Path] = {}
    for path in candidates:
        rev = _analogoperand_csv_revision(path)
        if rev:
            by_revision[rev] = path

    if not by_revision:
        return candidates[-1], 0, False

    if preferred_fw:
        target = firmware_version_to_csv_revision(preferred_fw)
        if target in by_revision:
            return by_revision[target], target, True
        lower = [rev for rev in by_revision if rev <= target]
        if lower:
            chosen = by_revision[max(lower)]
            return chosen, target, _analogoperand_csv_revision(chosen) == target
        # 5.x/6.x CSVs are absent from modern URPC; 740 is the closest IEC join
        # table until AnalogoperandTo61850_590.csv / _600.csv are sourced.
        if target < 700 and 740 in by_revision:
            return by_revision[740], target, False
        chosen = by_revision[min(by_revision)]
        return chosen, target, False

    chosen = by_revision[max(by_revision)]
    return chosen, _analogoperand_csv_revision(chosen), True


def _is_logic_operand_table(items: str) -> bool:
    """Return True if Items looks like the logic/contact/VO operand dictionary."""
    if not items or len(items) < 5000:
        return False
    if items.startswith("0 OFF") or ";0 OFF" in items or " OFF;" in items:
        if " ON;" in items or items.startswith("1 ON") or ";1 ON" in items:
            return "Virt Op" in items or "Cont Ip" in items or "Cont Op" in items
    return False


def _is_signal_operand_table(items: str) -> bool:
    """Return True if Items looks like the SRC/measurement operand dictionary."""
    if not items or len(items) < 3000:
        return False
    return "SRC1" in items and ("Ia RMS" in items or "DigCounter" in items)


def _find_flex_operand_items(root: ET.Element) -> str:
    """Return the Items string for the primary logic operand EnumType, if present."""
    for el in root.iter("EnumType"):
        if el.get("FormatIndex") == _FLEX_OPERAND_TABLE_INDEXES[0]:
            items = el.get("Items", "")
            if items:
                return items

    for el in root.iter("EnumType"):
        items = el.get("Items", "")
        if _is_logic_operand_table(items):
            return items
    return ""


def resolve_g60_flex_operand(
    operand: str, operand_map: dict[str, str]
) -> Optional[tuple[str, str]]:
    """Resolve a G30 flex operand to the G60 (FlexValue, display name) pair."""
    if not operand or not operand_map:
        return None

    if operand in operand_map:
        return operand_map[operand], operand

    if operand in ("OFF", "Off") or operand.upper() == "OFF":
        off_name = "OFF" if "OFF" in operand_map else operand
        return operand_map.get("OFF", "0"), off_name

    # Assign-virtual-output operands (= Name (VOn)) must not match VO read entries.
    if operand.startswith("= "):
        return None

    hw_match = _HW_ADDR_SUFFIX.search(operand)
    if not hw_match:
        return None

    hw_addr = hw_match.group(1)
    wants_on = _ON_WORD.search(operand) is not None
    wants_off = _OFF_WORD.search(operand) is not None

    candidates: list[tuple[str, str]] = []
    needle = f"({hw_addr})"
    for name, code in operand_map.items():
        if needle not in name:
            continue
        has_on = _ON_WORD.search(name) is not None
        has_off = _OFF_WORD.search(name) is not None
        if wants_on and not has_on:
            continue
        if wants_off and not has_off:
            continue
        if wants_on and has_off:
            continue
        if wants_off and has_on:
            continue
        candidates.append((name, code))

    if len(candidates) == 1:
        name, code = candidates[0]
        return code, name
    return None


def _flex_assign_vo_number(operand: str, g30_fv: int) -> Optional[int]:
    """Return the 1-based virtual-output index for a FlexLogic assign-VO write.

    G30 URS files from firmware 7.6x often store these as a bare G60-style
    code (``3276801``) without the ``= Name (VOn)`` display string that XML
    exports carry. Identify the VO from the name suffix when present, otherwise
    from the numeric code in either the legacy G30 or wide G60 range.
    """
    if operand.startswith("= "):
        vo_match = _ASSIGN_VO_SUFFIX.search(operand)
        if vo_match:
            return int(vo_match.group(1))
    if _G30_ASSIGN_VO_BASE <= g30_fv < _G30_ASSIGN_VO_BASE + _ASSIGN_VO_COUNT:
        return g30_fv - _G30_ASSIGN_VO_BASE
    if _G60_ASSIGN_VO_BASE <= g30_fv < _G60_ASSIGN_VO_BASE + _ASSIGN_VO_COUNT:
        return g30_fv - _G60_ASSIGN_VO_BASE
    return None


def remap_g30_flex_operand(
    operand: str,
    g30_flex_value: str,
    operand_map: dict[str, str],
    settings_flex_map: dict[str, str],
) -> Optional[tuple[str, str]]:
    """Translate a G30 flex operand to the G60 (value, FlexValue) pair."""
    try:
        g30_fv = int(g30_flex_value)
    except ValueError:
        return None

    if operand_map and operand in operand_map:
        return operand, operand_map[operand]

    if settings_flex_map and operand in settings_flex_map:
        return operand, settings_flex_map[operand]

    if operand in ("OFF", "Off") or operand.upper() == "OFF" or g30_fv == 0:
        off_value = "OFF" if operand_map and "OFF" in operand_map else operand
        off_code = operand_map.get("OFF", "0") if operand_map else "0"
        return off_value, off_code

    # FlexLogic assign-virtual-output: keep a G30 ``= Name (VOn)`` label when
    # present; 7.61+ URS exports often store only the bare write code
    # (``3276801``) with no display text. Unrecognized codes fall through to
    # the G60 template default (END / 2097152) and truncate the equation.
    vo_num = _flex_assign_vo_number(operand, g30_fv)
    if operand.startswith("= ") or vo_num is not None:
        if vo_num is None:
            return None
        display = operand if operand.startswith("= ") else f"= Virt Op {vo_num} (VO{vo_num})"
        return display, str(_G60_ASSIGN_VO_BASE + vo_num)

    if g30_fv in (_FLEXLOGIC_END_PACKED, _FLEXLOGIC_END_WIDE):
        return "END", str(_flexlogic_syntax_code(g30_fv))

    if operand_map:
        resolved = resolve_g60_flex_operand(operand, operand_map)
        if resolved is not None:
            flex_value, canonical_name = resolved
            return canonical_name, flex_value

    # FlexLogic syntax tokens (AND/OR/NOT/XOR/NAND/NOR/TIMER/END) encode an
    # opcode in the high byte and an input-count/index in the low byte. On G60
    # the opcode shifts to bits 16-23 while the count stays in the low byte:
    #   G60 = (opcode << 16) | count   (NOT plain G30 << 8, which corrupts the count)
    if _FLEXLOGIC_PRIMITIVE.match(operand):
        return operand, str(_flexlogic_syntax_code(g30_fv))

    return None


def _flexlogic_syntax_code(g30_fv: int) -> int:
    """Translate a G30 FlexLogic syntax-token code to its G60 equivalent.

    Older G30 exports pack opcode and count into 16 bits: (opcode << 8) | count.
    Newer G30 exports and G60 already store the wide form: (opcode << 16) | count.
    Shifting an already-wide value corrupts the code and breaks FlexLogic import.
    """
    if g30_fv > 0xFFFF:
        return g30_fv
    return ((g30_fv >> 8) << 16) | (g30_fv & 0xFF)


def is_ipv4_setting(label_id: str, template_value: str = "", raw_value: str = "") -> bool:
    """Return True for IPv4 address/mask/route Number settings."""
    label = label_id or ""
    if any(ex in label for ex in _IPV4_LABEL_EXCLUSIONS):
        return False
    if any(marker in label for marker in _IPV4_LABEL_MARKERS):
        return True
    if _DOTTED_IPV4_RE.match(template_value or "") or _DOTTED_IPV4_RE.match(raw_value or ""):
        return True
    return False


def parse_ipv4_octets(raw_value: str) -> Optional[tuple[int, int, int, int]]:
    """Parse dotted-quad or packed-uint32 IP into four octets."""
    raw = (raw_value or "").strip()
    if not raw:
        return None

    dotted = _DOTTED_IPV4_RE.match(raw)
    if dotted:
        octets = tuple(int(g) for g in dotted.groups())
        if all(0 <= o <= 255 for o in octets):
            return octets  # type: ignore[return-value]
        return None

    packed = _PACKED_IPV4_RE.match(raw)
    if packed:
        try:
            value = int(packed.group(1))
        except ValueError:
            return None
        if 0 <= value <= 0xFFFFFFFF:
            return (
                (value >> 24) & 0xFF,
                (value >> 16) & 0xFF,
                (value >> 8) & 0xFF,
                value & 0xFF,
            )
    return None


def format_ipv4_xml_value(raw_value: str) -> Optional[str]:
    """Format an IP as G60 XML Number value: width-3 space-padded dotted-quad."""
    octets = parse_ipv4_octets(raw_value)
    if octets is None:
        return None
    return ".".join(f"{octet:3d}" for octet in octets)


def format_ipv4_urs_value(raw_value: str) -> Optional[str]:
    """Format an IP as G60 URS DATA value: packed big-endian uint32 decimal."""
    octets = parse_ipv4_octets(raw_value)
    if octets is None:
        return None
    packed = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
    return str(packed)


def reformat_number_value(g30_value: str, g60_template_value: str) -> str:
    """Reformat a G30 Number value to match the decimal precision of the G60 template value.

    UR Setup rejects values whose decimal precision doesn't match the register's
    expected format (e.g. '1.00 Hz' when the G60 register stores '1.000 Hz').
    We keep the G30 numeric quantity but emit it with the G60's decimal places.

    IPv4 Number registers must not pass through this path — use
    ``format_ipv4_xml_value`` / ``format_ipv4_urs_value`` instead.
    """
    if is_ipv4_setting("", g60_template_value, g30_value):
        formatted = format_ipv4_xml_value(g30_value)
        if formatted is not None:
            return formatted

    g30m = _NUM_PATTERN.match(g30_value.strip())
    g60m = _NUM_PATTERN.match(g60_template_value.strip())
    if not g30m or not g60m:
        return g30_value
    try:
        num = float(g30m.group(1))
        g60_num_str = g60m.group(1)
        decimals = len(g60_num_str.split(".")[1]) if "." in g60_num_str else 0
        unit = g60m.group(2) or g30m.group(2)
        formatted = f"{num:.{decimals}f}"
        return f"{formatted} {unit}".strip() if unit else formatted
    except (ValueError, IndexError):
        return g30_value


def clean_value(value: str) -> str:
    """Remove control characters that are invalid in XML."""
    return "".join(c for c in value if c == "\t" or c == "\n" or c == "\r" or ord(c) >= 32)


def clean_tree(root: ET.Element) -> None:
    """Remove control characters from all text and attributes in the XML tree."""
    for el in root.iter():
        if el.text:
            el.text = clean_value(el.text)
        if el.tail:
            el.tail = clean_value(el.tail)
        for attr, value in list(el.attrib.items()):
            el.set(attr, clean_value(value))


def transfer_value(
    g60_el: ET.Element,
    g30_el: ET.Element,
    g60_flex_fv_map: Optional[dict] = None,
    g60_operand_map: Optional[dict[str, str]] = None,
    g30_signal_code_to_name: Optional[dict[str, str]] = None,
    g60_signal_name_to_code: Optional[dict[str, str]] = None,
) -> Optional[str]:
    stype = g60_el.get("SettingType", "")
    raw_g30_value = g30_el.get("value", g60_el.get("value", ""))
    g60_template_value = g60_el.get("value", "")
    g60_template_fv = g60_el.get("FlexValue", "")

    adjustment_note = None
    if (
        stype == "Number"
        and g60_el.get("labelID") == _USER_DISPLAY_ITEMS_LABEL
        and g30_signal_code_to_name is not None
        and g60_signal_name_to_code is not None
    ):
        remapped, adjustment_note = remap_user_display_item(
            raw_g30_value,
            g30_signal_code_to_name,
            g60_signal_name_to_code,
        )
        if remapped is not None:
            g60_el.set("value", clean_value(remapped))
        return adjustment_note

    if stype == "Number":
        label_id = g60_el.get("labelID", "")
        if is_ipv4_setting(label_id, g60_template_value, raw_g30_value):
            formatted_ip = format_ipv4_xml_value(raw_g30_value)
            if formatted_ip is not None:
                g60_el.set("value", clean_value(formatted_ip))
                return adjustment_note
            # Unparseable IP: keep template (safe default) rather than mangling.
            return (
                f"Unparseable IPv4 value {raw_g30_value!r}; kept G60 template default"
            )

        adjusted_value, adjustment_note = maybe_adjust_legacy_number_value(g60_el, raw_g30_value)
        if adjusted_value is not None:
            raw_g30_value = adjusted_value
        g60_el.set("value", clean_value(reformat_number_value(raw_g30_value, g60_template_value)))
    elif stype == "Enum":
        g60_el.set("value", clean_value(raw_g30_value))
        if "EnumValue" in g30_el.attrib:
            g60_el.set("EnumValue", clean_value(g30_el.get("EnumValue", "")))
    elif stype == "Flex":
        if "FlexValue" not in g30_el.attrib:
            g60_el.set("value", clean_value(raw_g30_value))
            return adjustment_note
        if g60_flex_fv_map is not None:
            g30_fv_raw = g30_el.get("FlexValue", "")
            remapped = remap_g30_flex_operand(
                raw_g30_value,
                g30_fv_raw,
                g60_operand_map or {},
                g60_flex_fv_map,
            )
            if remapped is not None:
                g60_value, g60_fv = remapped
                g60_el.set("value", clean_value(g60_value))
                g60_el.set("FlexValue", clean_value(g60_fv))
            else:
                # Operand not resolvable in this G60 hardware/firmware config.
                # Signal operands tied to absent hardware (e.g. SRC4 Ia RMS when SRC4
                # is not in the order code) revert to the G60 template default.
                if g30_el.get("FlexValue", "") == g60_template_fv:
                    g60_el.set("value", clean_value(raw_g30_value))
                    # FlexValue already equals template value; no change needed
                else:
                    # G30 operand unknown in this G60 config; keep template default
                    pass  # g60_el retains its template value and FlexValue unchanged
        else:
            g60_el.set("value", clean_value(raw_g30_value))
            g60_el.set("FlexValue", clean_value(g30_el.get("FlexValue", "")))
    else:
        g60_el.set("value", clean_value(raw_g30_value))

    return adjustment_note


# ΓöÇΓöÇ Core conversion ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

def convert(
    g30_path: Path,
    g60_template_path: Path,
    output_dir: Path,
) -> VerificationResult:
    print(f"Reading G30      : {g30_path}")
    g30_root = validate_g30_settings_xml(g30_path)
    print(f"Reading G60      : {g60_template_path}")
    g60_root = parse_xml(g60_template_path)

    # ΓöÇΓöÇ Derive output device name and file names ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    g30_device_name_raw = g30_root.get("deviceName", "")
    g60_device_name_raw = g60_root.get("deviceName", "")

    # Combine: take the G30 project identity, swap in the G60 model from orderCode
    g60_order_code = g60_root.get("orderCode", "")
    g60_version = g60_root.get("version", "")
    output_device_name_raw = derive_output_device_name(g30_device_name_raw, g60_order_code)
    output_device_name = ur_title_case(output_device_name_raw)
    output_file_stem = derive_output_file_stem(output_device_name, g60_version)

    output_xml_path  = output_dir / f"{output_file_stem}.xml"
    output_html_path = output_dir / f"{output_file_stem}_OR.html"

    print(f"Output device    : {output_device_name}")
    print(f"Output firmware  : {g60_version or '(unknown)'}")
    print(f"Output XML       : {output_xml_path.name}")
    print(f"Output report    : {output_html_path.name}")
    print()

    g30_info = {
        "version": g30_root.get("version", ""),
        "orderCode": g30_root.get("orderCode", ""),
        "deviceName": g30_device_name_raw,
        "deviceName_display": ur_title_case(g30_device_name_raw),
        "URSetupVersion": g30_root.get("URSetupVersion", ""),
        "TimeCreated": g30_root.get("TimeCreated", ""),
    }
    g60_info = {
        "version": g60_root.get("version", ""),
        "orderCode": g60_root.get("orderCode", ""),
        "deviceName": g60_device_name_raw,
        "deviceName_display": ur_title_case(g60_device_name_raw),
        "URSetupVersion": g60_root.get("URSetupVersion", ""),
        "TimeCreated": g60_root.get("TimeCreated", ""),
    }

    # Keep the G60 template's deviceName in the output XML root.
    # This preserves the exported device identity expected by the real relay.
    g30_lookup = build_lookup(g30_root)
    g60_path_map = build_path_map(g60_root)
    g30_path_map = build_path_map(g30_root)

    # Map G60 operand names to their firmware-internal FlexValue codes.
    # FlexValue is NOT the same between G30 7.x and G60 8.x firmware for
    # protection-element outputs and system operands. Hardware-address operands
    # (contacts, virtual outputs) also use different codes but are matched by the
    # address suffix in the operand table, e.g. (H8a) or (VO4).
    g60_flex_fv_map: dict[str, str] = {}
    for s in g60_root.iter("Setting"):
        if s.get("SettingType") == "Flex":
            op = s.get("value", "")
            fv = s.get("FlexValue", "")
            if op and fv and op not in g60_flex_fv_map:
                g60_flex_fv_map[op] = fv

    g60_operand_map = build_flex_operand_map(g60_root)
    if g60_operand_map:
        print(f"  Flex operand table : {len(g60_operand_map):,} entries loaded from G60 template")
    else:
        print("  Warning: Flex operand table not found in G60 template; LED/Flex remapping may fail",
              file=sys.stderr)

    g30_signal_code_to_name, _ = build_signal_operand_tables(g30_root)
    _, g60_signal_name_to_code = build_signal_operand_tables(g60_root)
    if g30_signal_code_to_name and g60_signal_name_to_code:
        print(
            f"  User display table : {len(g30_signal_code_to_name):,} G30 signals, "
            f"{len(g60_signal_name_to_code):,} G60 signals"
        )
    else:
        print(
            "  Warning: Signal operand table (10013) not found; user display Item remapping may fail",
            file=sys.stderr,
        )

    transferred_records: list[TransferredRecord] = []
    g60_only_records: list[G60OnlyRecord] = []
    transferred_keys: set[tuple[str, str, str, str, str]] = set()

    for setting in g60_root.iter("Setting"):
        key = (
            setting.get("labelID", ""),
            setting.get("group", "0"),
            setting.get("module", "0"),
            setting.get("item", "0"),
            setting.get("bit", "0"),
        )
        path = g60_path_map.get(id(setting), "")

        g30_match = g30_lookup.get(key)
        if g30_match is not None:
            g60_template_value = setting.get("value", "")
            rec = TransferredRecord(
                path=path,
                label_id=key[0],
                group=key[1],
                module=key[2],
                item=key[3],
                bit=key[4],
                g60_name=setting.get("screenName", ""),
                g30_name=g30_match.get("screenName", ""),
                setting_type=setting.get("SettingType", ""),
                g30_value=g30_match.get("value", ""),
                original_g30_value=g30_match.get("value", ""),
                g60_template_value=g60_template_value,
            )
            rec.adjustment_note = transfer_value(
                setting,
                g30_match,
                g60_flex_fv_map,
                g60_operand_map,
                g30_signal_code_to_name,
                g60_signal_name_to_code,
            )
            rec.g30_value = setting.get("value", "")
            if setting.get("SettingType") == "Number":
                rec.range_warning = check_range(setting, rec.g30_value)

            transferred_records.append(rec)
            transferred_keys.add(key)
        else:
            g60_only_records.append(G60OnlyRecord(
                path=path,
                label_id=key[0],
                group=key[1],
                module=key[2],
                name=setting.get("screenName", ""),
                setting_type=setting.get("SettingType", ""),
                default_value=setting.get("value", ""),
            ))

    # G30 settings not present in G60
    g60_keys = {
        (s.get("labelID", ""), s.get("group", "0"), s.get("module", "0"),
         s.get("item", "0"), s.get("bit", "0"))
        for s in g60_root.iter("Setting")
    }
    dropped_records: list[DroppedRecord] = []
    for key, el in g30_lookup.items():
        if key not in g60_keys:
            dropped_records.append(DroppedRecord(
                path=g30_path_map.get(id(el), ""),
                label_id=key[0],
                group=key[1],
                module=key[2],
                name=el.get("screenName", ""),
                setting_type=el.get("SettingType", ""),
                value=el.get("value", ""),
            ))
    dropped_records.sort(key=lambda r: (r.path, r.name))

    # Derived subsets
    value_changes = [r for r in transferred_records if r.value_changed]
    name_diffs = [r for r in transferred_records if r.name_changed]
    range_warnings = [r for r in transferred_records if r.range_warning]
    auto_adjusted = [r for r in transferred_records if r.adjustment_note]
    unchanged = [r for r in transferred_records if not r.value_changed]

    # ΓöÇΓöÇ Console summary ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    SEP = "-" * 60
    print(f"\n{SEP}")
    print(f"  Total G30 settings              : {len(g30_lookup)}")
    print(f"  Transferred G30 -> G60          : {len(transferred_records)}")
    print(f"    of which values changed       : {len(value_changes)}")
    print(f"    of which names differ         : {len(name_diffs)}")
    if auto_adjusted:
        print(f"    of which were auto-adjusted   : {len(auto_adjusted)}")
    print(f"  G60-only (kept at defaults)     : {len(g60_only_records)}")
    print(f"  G30-only (dropped)              : {len(dropped_records)}")
    if range_warnings:
        print(f"\n  WARNING: {len(range_warnings)} out-of-range value(s) -- review HTML report")
    print(f"{SEP}\n")

    # Clean the tree of control characters
    clean_tree(g60_root)

    # ΓöÇΓöÇ Safety: refuse to overwrite input files ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    input_paths = {g30_path.resolve(), g60_template_path.resolve()}
    for out in (output_xml_path, output_html_path):
        if out.resolve() in input_paths:
            print(f"ERROR: Output path '{out}' would overwrite an input file.", file=sys.stderr)
            print("       Pass a different output directory as the third argument.", file=sys.stderr)
            sys.exit(1)

    # ΓöÇΓöÇ Write output XML ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    output_dir.mkdir(parents=True, exist_ok=True)
    ET.indent(g60_root, space="\t")
    xml_body = ET.tostring(g60_root, encoding="unicode")
    xml_out = '<?xml version="1.0" ?>\n' + xml_body + "\n"
    with open(output_xml_path, "wb") as f:
        f.write(xml_out.encode("utf-16-le"))
    print(f"XML  written : {output_xml_path.name}  ({output_xml_path.stat().st_size:,} bytes)")

    # ΓöÇΓöÇ Write HTML report ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    html = build_html_report(
        g30_path=g30_path,
        g60_template_path=g60_template_path,
        output_xml_path=output_xml_path,
        output_device_name=output_device_name,
        g30_info=g30_info,
        g60_info=g60_info,
        transferred=transferred_records,
        value_changes=value_changes,
        name_diffs=name_diffs,
        range_warnings=range_warnings,
        unchanged=unchanged,
        g60_only=g60_only_records,
        dropped=dropped_records,
    )
    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML written : {output_html_path.name}  ({output_html_path.stat().st_size:,} bytes)")

    from verify_conversion import verify_xml_tree

    verification: VerificationResult = verify_xml_tree(
        g60_root,
        output_xml_path,
        transferred_keys=transferred_keys,
    )
    print(f"  Post-write verification : {verification.format_summary()}")
    return verification


# ΓöÇΓöÇ HTML report builder ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

def e(text: str) -> str:
    """HTML-escape a string."""
    return html_lib.escape(str(text))


def badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{e(text)}</span>'


def key_cell(rec) -> str:
    return (f'<td class="mono small">{e(rec.label_id)}<br>'
            f'<span class="dim">g={e(rec.group)} m={e(rec.module)} '
            f'i={e(rec.item)} b={e(rec.bit)}</span></td>')


def path_cell(path: str) -> str:
    parts = [p.strip() for p in path.split(">") if p.strip()]
    return '<td class="path">' + " <span class='sep'>&rsaquo;</span> ".join(
        f"<span>{e(p)}</span>" for p in parts
    ) + "</td>"


def value_cell(val: str, cls: str = "") -> str:
    return f'<td class="mono {cls}">{e(val)}</td>'


def type_badge(stype: str) -> str:
    cls = {"Number": "type-num", "Enum": "type-enum", "Flex": "type-flex"}.get(stype, "type-other")
    return badge(stype, cls)


def build_html_report(
    g30_path, g60_template_path, output_xml_path,
    output_device_name,
    g30_info, g60_info,
    transferred, value_changes, name_diffs, range_warnings, unchanged,
    g60_only, dropped,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_g30 = len(transferred) + len(dropped)
    total_g60 = len(transferred) + len(g60_only)

    def section(title: str, count: int, icon: str, color: str, content: str, sid: str,
                collapsed: bool = False) -> str:
        state = ' collapsed' if collapsed else ''
        return f"""
<section class="card{state}" id="{sid}">
  <div class="card-header" onclick="toggle('{sid}')">
    <span class="card-icon">{icon}</span>
    <span class="card-title">{e(title)}</span>
    <span class="card-count {color}">{count:,}</span>
    <span class="chevron">&#x25BE;</span>
  </div>
  <div class="card-body">
{content}
  </div>
</section>"""

    def table(headers: list[str], rows: list[str], tid: str = "", searchable: bool = True) -> str:
        search_bar = ""
        if searchable and rows:
            search_bar = f'<input class="search-box" placeholder="Filter rows..." oninput="filterTable(this)" />'
        if not rows:
            return f'<p class="empty">No entries.</p>'
        hdr = "".join(f"<th>{e(h)}</th>" for h in headers)
        body = "\n".join(rows)
        tid_attr = f' id="{tid}"' if tid else ""
        return f"""
{search_bar}
<div class="table-wrap">
<table{tid_attr}>
  <thead><tr>{hdr}</tr></thead>
  <tbody>{body}</tbody>
</table>
</div>"""

    # ΓöÇΓöÇ Section: Value changes ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    vc_rows = []
    for r in sorted(value_changes, key=lambda x: (x.path, x.g60_name)):
        warn = f' <span class="warn-icon" title="{e(r.range_warning)}">&#9888;</span>' if r.range_warning else ""
        name_note = (f'<br><span class="dim">G30 name: {e(r.g30_name)}</span>'
                     if r.name_changed else "")
        adj_note = (f'<br><span class="dim">{e(r.adjustment_note)}</span>'
                    if r.adjustment_note else "")
        vc_rows.append(
            f"<tr>"
            f"{path_cell(r.path)}"
            f"<td>{e(r.g60_name)}{name_note}{warn}{adj_note}</td>"
            f"{key_cell(r)}"
            f"<td>{type_badge(r.setting_type)}</td>"
            f"{value_cell(r.g60_template_value, 'old-val')}"
            f"{value_cell(r.g30_value, 'new-val')}"
            f"</tr>"
        )
    vc_content = table(
        ["Location (G60)", "Setting Name", "Label ID / Key", "Type",
         "G60 Template Value", "G30 Value (applied)"],
        vc_rows, "tbl-vc"
    )

    # ΓöÇΓöÇ Section: Name differences ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    nd_rows = []
    for r in sorted(name_diffs, key=lambda x: (x.path, x.g60_name)):
        vc_note = (f' <span class="badge badge-changed">value also changed</span>'
                   if r.value_changed else "")
        nd_rows.append(
            f"<tr>"
            f"{path_cell(r.path)}"
            f"<td>{e(r.g60_name)}</td>"
            f"<td>{e(r.g30_name)}</td>"
            f"{key_cell(r)}"
            f"<td>{type_badge(r.setting_type)}{vc_note}</td>"
            f"</tr>"
        )
    nd_content = table(
        ["Location (G60)", "G60 Name", "G30 Name", "Label ID / Key", "Type"],
        nd_rows, "tbl-nd"
    )

    auto_adjusted = [r for r in transferred if r.adjustment_note]
    aa_rows = []
    for r in sorted(auto_adjusted, key=lambda x: (x.path, x.g60_name)):
        aa_rows.append(
            f"<tr>"
            f"{path_cell(r.path)}"
            f"<td>{e(r.g60_name)}</td>"
            f"{key_cell(r)}"
            f"{value_cell(r.original_g30_value or r.g30_value)}"
            f"{value_cell(r.g30_value, 'new-val')}"
            f"<td>{e(r.adjustment_note or '')}</td>"
            f"</tr>"
        )
    aa_content = table(
        ["Location (G60)", "Setting Name", "Label ID / Key", "Original G30 Value", "Adjusted G60 Value", "Note"],
        aa_rows, "tbl-aa"
    )

    # ΓöÇΓöÇ Section: Range warnings ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    rw_rows = []
    for r in range_warnings:
        rw_rows.append(
            f"<tr>"
            f"{path_cell(r.path)}"
            f"<td>{e(r.g60_name)}</td>"
            f"{key_cell(r)}"
            f"{value_cell(r.g30_value, 'warn-val')}"
            f"<td>{e(r.range_warning)}</td>"
            f"</tr>"
        )
    rw_content = table(
        ["Location (G60)", "Setting Name", "Label ID / Key", "G30 Value Applied", "Range Issue"],
        rw_rows, "tbl-rw"
    )

    # ΓöÇΓöÇ Section: G60-only ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    go_rows = []
    for r in sorted(g60_only, key=lambda x: (x.path, x.name)):
        go_rows.append(
            f"<tr>"
            f"{path_cell(r.path)}"
            f"<td>{e(r.name)}</td>"
            f'<td class="mono small">{e(r.label_id)}<br>'
            f'<span class="dim">g={e(r.group)} m={e(r.module)}</span></td>'
            f"<td>{type_badge(r.setting_type)}</td>"
            f"{value_cell(r.default_value)}"
            f"</tr>"
        )
    go_content = table(
        ["Location (G60)", "Setting Name", "Label ID / Key", "Type", "Default Value Kept"],
        go_rows, "tbl-go"
    )

    # ΓöÇΓöÇ Section: Dropped ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    dr_rows = []
    for r in dropped:
        dr_rows.append(
            f"<tr>"
            f"{path_cell(r.path)}"
            f"<td>{e(r.name)}</td>"
            f'<td class="mono small">{e(r.label_id)}<br>'
            f'<span class="dim">g={e(r.group)} m={e(r.module)}</span></td>'
            f"<td>{type_badge(r.setting_type)}</td>"
            f"{value_cell(r.value)}"
            f"</tr>"
        )
    dr_content = table(
        ["Location (G30)", "Setting Name", "Label ID / Key", "Type", "G30 Value (lost)"],
        dr_rows, "tbl-dr"
    )

    # ΓöÇΓöÇ Section: Unchanged transfers ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    uc_rows = []
    for r in sorted(unchanged, key=lambda x: (x.path, x.g60_name)):
        uc_rows.append(
            f"<tr>"
            f"{path_cell(r.path)}"
            f"<td>{e(r.g60_name)}</td>"
            f"{key_cell(r)}"
            f"<td>{type_badge(r.setting_type)}</td>"
            f"{value_cell(r.g30_value)}"
            f"</tr>"
        )
    uc_content = table(
        ["Location (G60)", "Setting Name", "Label ID / Key", "Type", "Value (unchanged)"],
        uc_rows, "tbl-uc"
    )

    # ΓöÇΓöÇ Assemble body sections ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    range_section = ""
    if range_warnings:
        range_section = section(
            "Range Warnings ΓÇö Values Outside G60 Valid Range",
            len(range_warnings), "&#9888;", "color-warn", rw_content, "sec-rw"
        )

    auto_adjusted_section = ""
    if auto_adjusted:
        auto_adjusted_section = section(
            "Automatic Adjustments ΓÇö Legacy G30 values scaled for G60",
            len(auto_adjusted), "&#9888;", "color-warn", aa_content, "sec-aa"
        )

    sections_html = (
        range_section +
        section("Value Changes ΓÇö G30 Value Differs from G60 Template Default",
                len(value_changes), "&#8644;", "color-changed", vc_content, "sec-vc") +
        auto_adjusted_section +
        section("Setting Name Differences ΓÇö Same Register, Different Display Name",
                len(name_diffs), "&#8756;", "color-name", nd_content, "sec-nd") +
        section("G60-Only Settings ΓÇö New in G60, Kept at Template Defaults",
                len(g60_only), "&#8853;", "color-g60only", go_content, "sec-go") +
        section("Dropped G30 Settings ΓÇö No Equivalent in G60",
                len(dropped), "&#10007;", "color-dropped", dr_content, "sec-dr") +
        section("Transferred Unchanged ΓÇö Same Value in Both Files",
                len(unchanged), "&#10003;", "color-ok", uc_content, "sec-uc", collapsed=True)
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>G30 to G60 Conversion Report</title>
<style>
  :root {{
    --bg: #f4f6f9;
    --card: #ffffff;
    --border: #dde1e8;
    --text: #1a1d23;
    --dim: #6b7280;
    --mono: 'Consolas', 'Courier New', monospace;
    --changed: #1d6fb8;
    --ok: #1a7a44;
    --warn: #b45309;
    --dropped: #b91c1c;
    --g60only: #6d28d9;
    --name: #0e7490;
    --radius: 8px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: var(--bg);
          color: var(--text); font-size: 14px; line-height: 1.5; }}
  a {{ color: var(--changed); }}

  /* ΓöÇΓöÇ Header ΓöÇΓöÇ */
  .page-header {{ background: #1a2744; color: #fff; padding: 28px 40px 22px; }}
  .page-header h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 4px; }}
  .page-header .sub {{ color: #9aa8c0; font-size: 13px; }}
  .meta-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
                margin-top: 20px; }}
  .meta-box {{ background: rgba(255,255,255,0.07); border-radius: var(--radius);
               padding: 14px 18px; }}
  .meta-box h3 {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
                  color: #7d9cc0; margin-bottom: 8px; }}
  .meta-box table {{ width: 100%; border-collapse: collapse; }}
  .meta-box td {{ padding: 2px 0; color: #c8d8f0; font-size: 13px; }}
  .meta-box td:first-child {{ color: #7d9cc0; width: 130px; font-size: 12px; }}

  /* ΓöÇΓöÇ Summary bar ΓöÇΓöÇ */
  .summary-bar {{ display: flex; gap: 12px; flex-wrap: wrap;
                  padding: 20px 40px; border-bottom: 1px solid var(--border); }}
  .stat-card {{ background: var(--card); border: 1px solid var(--border);
                border-radius: var(--radius); padding: 14px 20px; min-width: 140px;
                flex: 1; }}
  .stat-card .num {{ font-size: 28px; font-weight: 700; line-height: 1; }}
  .stat-card .lbl {{ font-size: 12px; color: var(--dim); margin-top: 4px; }}
  .c-changed {{ color: var(--changed); }}
  .c-ok      {{ color: var(--ok); }}
  .c-warn    {{ color: var(--warn); }}
  .c-dropped {{ color: var(--dropped); }}
  .c-g60only {{ color: var(--g60only); }}
  .c-name    {{ color: var(--name); }}

  /* ΓöÇΓöÇ Sections / cards ΓöÇΓöÇ */
  .content {{ padding: 24px 40px 48px; max-width: 1600px; }}
  .card {{ background: var(--card); border: 1px solid var(--border);
           border-radius: var(--radius); margin-bottom: 16px;
           overflow: hidden; }}
  .card-header {{ display: flex; align-items: center; gap: 10px; padding: 14px 20px;
                  cursor: pointer; user-select: none;
                  border-bottom: 1px solid var(--border); }}
  .card.collapsed .card-body {{ display: none; }}
  .card.collapsed .card-header {{ border-bottom: none; }}
  .card-icon {{ font-size: 16px; flex-shrink: 0; }}
  .card-title {{ flex: 1; font-weight: 600; font-size: 14px; }}
  .card-count {{ font-size: 20px; font-weight: 700; min-width: 50px; text-align: right; }}
  .chevron {{ font-size: 12px; color: var(--dim); transition: transform .2s; }}
  .card.collapsed .chevron {{ transform: rotate(-90deg); }}
  .color-changed {{ color: var(--changed); }}
  .color-ok      {{ color: var(--ok); }}
  .color-warn    {{ color: var(--warn); }}
  .color-dropped {{ color: var(--dropped); }}
  .color-g60only {{ color: var(--g60only); }}
  .color-name    {{ color: var(--name); }}
  .card-body {{ padding: 16px 20px; overflow-x: auto; }}
  .empty {{ color: var(--dim); font-style: italic; padding: 8px 0; }}

  /* ΓöÇΓöÇ Search ΓöÇΓöÇ */
  .search-box {{ width: 100%; max-width: 400px; padding: 7px 12px; margin-bottom: 12px;
                 border: 1px solid var(--border); border-radius: 6px;
                 font-size: 13px; color: var(--text); background: var(--bg); }}
  .search-box:focus {{ outline: none; border-color: var(--changed); }}

  /* ΓöÇΓöÇ Tables ΓöÇΓöÇ */
  .table-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #f0f3f8; font-weight: 600; font-size: 12px;
        text-transform: uppercase; letter-spacing: .04em;
        padding: 8px 12px; text-align: left; border-bottom: 2px solid var(--border);
        white-space: nowrap; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid #eef0f4; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8faff; }}
  tr.hidden {{ display: none; }}

  /* ΓöÇΓöÇ Cell types ΓöÇΓöÇ */
  .path {{ font-size: 12px; color: var(--dim); white-space: nowrap; }}
  .path .sep {{ color: #bbb; margin: 0 2px; }}
  .mono {{ font-family: var(--mono); font-size: 12px; }}
  .small {{ font-size: 11px; }}
  .dim {{ color: var(--dim); font-size: 11px; }}
  .old-val {{ color: var(--dropped); font-family: var(--mono); font-size: 12px; }}
  .new-val {{ color: var(--ok); font-family: var(--mono); font-size: 12px; font-weight: 600; }}
  .warn-val {{ color: var(--warn); font-family: var(--mono); font-size: 12px; font-weight: 600; }}
  .warn-icon {{ color: var(--warn); cursor: help; }}

  /* ΓöÇΓöÇ Badges ΓöÇΓöÇ */
  .badge {{ display: inline-block; font-size: 10px; font-weight: 700;
            padding: 2px 6px; border-radius: 4px; text-transform: uppercase;
            letter-spacing: .04em; }}
  .type-num   {{ background: #dbeafe; color: #1e40af; }}
  .type-enum  {{ background: #ede9fe; color: #5b21b6; }}
  .type-flex  {{ background: #d1fae5; color: #065f46; }}
  .type-other {{ background: #f3f4f6; color: #374151; }}
  .badge-changed {{ background: #dbeafe; color: var(--changed); }}

  @media print {{
    body {{ background: white; }}
    .page-header {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    .card {{ break-inside: avoid; }}
    .search-box {{ display: none; }}
  }}
</style>
</head>
<body>

<div class="page-header">
  <h1>GE Multilin UR &mdash; G30 to G60 Conversion Report</h1>
  <div class="sub">Generated {e(now)}</div>
  <div class="meta-grid">
    <div class="meta-box">
      <h3>G30 Source (Original)</h3>
      <table>
        <tr><td>File</td><td>{e(g30_path.name)}</td></tr>
        <tr><td>Firmware ver.</td><td>{e(g30_info['version'])}</td></tr>
        <tr><td>Order code</td><td>{e(g30_info['orderCode'])}</td></tr>
        <tr><td>Device name</td><td>{e(g30_info['deviceName_display'])}</td></tr>
        <tr><td>URSetup ver.</td><td>{e(g30_info['URSetupVersion'])}</td></tr>
        <tr><td>File created</td><td>{e(g30_info['TimeCreated'])}</td></tr>
      </table>
    </div>
    <div class="meta-box">
      <h3>G60 Template &rarr; Output</h3>
      <table>
        <tr><td>Template file</td><td>{e(g60_template_path.name)}</td></tr>
        <tr><td>Output XML</td><td>{e(output_xml_path.name)}</td></tr>
        <tr><td>Output report</td><td>{e(output_xml_path.stem)}_OR.html</td></tr>
        <tr><td>Output device name</td><td>{e(output_device_name)}</td></tr>
        <tr><td>Firmware ver.</td><td>{e(g60_info['version'])} <span style="color:#9aa8c0;font-size:11px;">(preserved)</span></td></tr>
        <tr><td>Order code</td><td>{e(g60_info['orderCode'])} <span style="color:#9aa8c0;font-size:11px;">(preserved)</span></td></tr>
        <tr><td>URSetup ver.</td><td>{e(g60_info['URSetupVersion'])}</td></tr>
      </table>
    </div>
  </div>
</div>

<div class="summary-bar">
  <div class="stat-card">
    <div class="num c-ok">{len(transferred):,}</div>
    <div class="lbl">Settings transferred</div>
  </div>
  <div class="stat-card">
    <div class="num c-changed">{len(value_changes):,}</div>
    <div class="lbl">Values changed from template</div>
  </div>
  <div class="stat-card">
    <div class="num c-name">{len(name_diffs):,}</div>
    <div class="lbl">Name differences</div>
  </div>
  <div class="stat-card">
    <div class="num c-g60only">{len(g60_only):,}</div>
    <div class="lbl">G60-only (defaults kept)</div>
  </div>
  <div class="stat-card">
    <div class="num c-dropped">{len(dropped):,}</div>
    <div class="lbl">G30 settings dropped</div>
  </div>
  {'<div class="stat-card"><div class="num c-warn">' + str(len(range_warnings)) + '</div><div class="lbl">Range warnings</div></div>' if range_warnings else ''}
</div>

<div class="content">
{sections_html}
</div>

<script>
function toggle(id) {{
  document.getElementById(id).classList.toggle('collapsed');
}}
function filterTable(input) {{
  const q = input.value.toLowerCase();
  const wrap = input.nextElementSibling;
  const rows = wrap ? wrap.querySelectorAll('tbody tr') : [];
  rows.forEach(r => {{
    r.classList.toggle('hidden', !r.textContent.toLowerCase().includes(q));
  }});
}}
</script>
</body>
</html>"""


# ΓöÇΓöÇ Entry point ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

if __name__ == "__main__":
    here = Path(__file__).parent

    # Developer CLI only — end users should run G30-to-G60-Converter.exe.
    #   python convert_g30_to_g60.py  <g30_source.xml>  [output_dir]
    if len(sys.argv) < 2:
        print("Usage:  python convert_g30_to_g60.py  <g30_source.xml>  [output_dir]")
        print(f"  G60 template : bases/G60 Base.xml (under script folder)")
        print(f"  Output dir   : {here / 'Converted'}  (default)")
        sys.exit(0)

    g30_path  = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else here / "Converted"

    if not g30_path.exists():
        print(f"ERROR: File not found: {g30_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading: {g30_path.name}")
    g60_template_path = select_base_template(g30_path, here)

    if not g60_template_path.exists():
        print(f"ERROR: Base template not found: {g60_template_path}", file=sys.stderr)
        sys.exit(1)

    convert(g30_path, g60_template_path, output_dir)
