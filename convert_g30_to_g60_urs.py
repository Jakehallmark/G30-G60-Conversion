"""
G30 to G60 relay settings converter for GE Multilin UR series .urs files.

Produces a converted G60 .urs file by:
  1. Parsing the G30 source .urs (register values)
  2. Using a G60 template .urs for register numbers and URPC structure
  3. Using a G60 Base.xml template for setting types and value remapping logic
  4. Writing a new .urs with G60 register map and transferred values

User-display Items are remapped without requiring a companion G30 .xml by
joining bundled AnalogoperandTo61850_*.csv tables under firmware/g30 and
firmware/g60 on IEC61850 path stems, then resolving through the G60 Base
signal table (EnumType 10013). A companion XML remains an optional override.

Usage:
  python convert_g30_to_g60_urs.py  <g30_source.urs>  [output_dir]
      [--firmware 860|8.5x|...]  [--g60-xml PATH]  [--g60-urs PATH]
"""

from __future__ import annotations

import copy
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from aio.base_templates import resolve_base_template

from convert_g30_to_g60 import (
    _G60_ASSIGN_VO_BASE,
    _G60_USER_DISPLAY_CODE_OFFSET,
    _USER_DISPLAY_ITEMS_LABEL,
    _flex_assign_vo_number,
    build_flex_operand_map,
    build_g30_to_g60_signal_names_via_analogoperands,
    build_lookup,
    build_signal_code_map_from_urs_annotations,
    build_signal_operand_tables,
    derive_g30_identity_from_urs_stem,
    derive_output_device_name,
    derive_output_file_stem,
    format_ipv4_urs_value,
    is_ipv4_setting,
    maybe_adjust_legacy_number_value,
    parse_coded_urs_display,
    parse_xml,
    reformat_number_value,
    resolve_analogoperand_csv,
    resolve_analogoperand_csv_detailed,
    transfer_value,
    ur_title_case,
)
from urs_io import (
    UrsDataRow,
    UrsFile,
    find_companion_xml,
    make_setting_element,
    parse_urs_file,
    resolve_urs_template,
    setting_attrs_to_urs_value,
    update_header_for_output,
    write_urs_file,
)
from verify_conversion import VerificationResult, verify_urs_rows

_CODED_VALUE_RE = re.compile(r"^(\d+)\s+\((.+)\)\s*$", re.DOTALL)


@dataclass
class UrsConversionStats:
  transferred: int = 0
  kept_default: int = 0
  dropped_g30: int = 0
  auto_adjusted: int = 0


@dataclass
class UrsConvertResult:
    output_path: Path
    verification: VerificationResult



def resolve_g60_templates(
    app_dir: Path,
    *,
    firmware: Optional[str] = None,
    g60_xml_path: Optional[Path] = None,
    g60_urs_path: Optional[Path] = None,
) -> tuple[Path, Path]:
    """Resolve paired G60 XML and URS base templates."""
    base_dir = app_dir / "bases"
    if g60_xml_path is not None:
        xml_path = g60_xml_path
    else:
        xml_path = resolve_base_template(base_dir, firmware).path

    urs_path = resolve_urs_template(xml_path, base_dir, g60_urs_path)
    return xml_path, urs_path


def app_root_dir() -> Path:
    """Repo root in development, or PyInstaller extract root when frozen."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def firmware_dir(app_dir: Optional[Path] = None) -> Path:
    """Directory containing bundled Analogoperand CSVs / optional firmware binaries."""
    return (app_dir or app_root_dir()) / "firmware"


def load_g30_signal_code_to_name(
    g30_urs: UrsFile,
    g60_work,
    app_dir: Path,
    *,
    companion_xml: Optional[Path] = None,
    g30_version: str = "",
    g60_version: str = "",
) -> tuple[dict[str, str], str]:
    """Resolve G30 user-display code→name without requiring a per-site companion XML.

    Priority:
      1. Companion G30 settings XML (full EnumType 10013), when present
      2. AnalogoperandTo61850 CSV join under firmware/g30 + firmware/g60
      3. Sparse URS ``code (name)`` annotations that match G60 signal names

    Returns (code_to_name, source_description).
    """
    g60_code_to_name, g60_name_to_code = build_signal_operand_tables(g60_work)

    if companion_xml is not None and companion_xml.exists():
        g30_root = parse_xml(companion_xml)
        code_to_name, _ = build_signal_operand_tables(g30_root)
        if code_to_name:
            return code_to_name, f"companion XML ({companion_xml.name})"

    fw = firmware_dir(app_dir)
    g30_csv, g30_expected_rev, g30_exact = resolve_analogoperand_csv_detailed(
        fw / "g30", preferred_fw=g30_version
    )
    g60_csv, g60_expected_rev, g60_exact = resolve_analogoperand_csv_detailed(
        fw / "g60", preferred_fw=g60_version
    )
    code_to_name: dict[str, str] = {}
    source = "unavailable"

    if g30_csv and g60_csv and g60_code_to_name:
        code_to_name = build_g30_to_g60_signal_names_via_analogoperands(
            g30_csv, g60_csv, g60_code_to_name
        )
        if code_to_name:
            source = f"Analogoperand CSVs ({g30_csv.name} + {g60_csv.name})"
            if g30_version and not g30_exact:
                source += (
                    f" [fallback: expected AnalogoperandTo61850_{g30_expected_rev}.csv]"
                )
            if g60_version and not g60_exact:
                source += (
                    f" [fallback: expected AnalogoperandTo61850_{g60_expected_rev}.csv]"
                )

    # Supplement with any annotated URS values whose display name exists on G60.
    annotated: list[tuple[str, str]] = []
    for row in g30_urs.data_rows:
        code, name = parse_coded_urs_display(row.value)
        if code and name:
            annotated.append((code, name))
    from_urs = build_signal_code_map_from_urs_annotations(annotated, g60_name_to_code)
    if from_urs:
        for code, name in from_urs.items():
            code_to_name.setdefault(code, name)
        if source == "unavailable":
            source = "URS annotations"
        else:
            source = f"{source} + URS annotations"

    return code_to_name, source


def _flex_code_to_name(operand_map: dict[str, str]) -> dict[str, str]:
    """Invert name->code operand map to code->name (first name wins)."""
    reverse: dict[str, str] = {}
    for name, code in operand_map.items():
        reverse.setdefault(code, name)
    return reverse


def _parse_coded_urs_value(urs_value: str) -> tuple[str, str]:
    match = _CODED_VALUE_RE.match(urs_value)
    if match:
        return match.group(1), match.group(2)
    return "", urs_value


def _g30_virtual_output_names(g30_urs: UrsFile) -> dict[int, str]:
    """Map VO number → user-defined name from G30 VIRTUAL_OUTPUT_X_NAME rows."""
    names: dict[int, str] = {}
    for row in g30_urs.data_rows:
        if row.label_id != "UR_DATA_VIRTUAL_OUTPUT_X_NAME":
            continue
        try:
            vo_num = int(row.module)
        except ValueError:
            continue
        name = (row.value or "").strip()
        if vo_num and name:
            names[vo_num] = name
    return names


def _decorate_bare_assign_vo(g30_el, vo_names: dict[int, str]) -> None:
    """Replace a bare assign-VO code with ``= Name (VOn)`` so remap keeps the label."""
    operand = g30_el.get("value", "") or ""
    if operand.startswith("= "):
        return
    try:
        fv = int(g30_el.get("FlexValue", ""))
    except ValueError:
        return
    vo_num = _flex_assign_vo_number(operand, fv)
    if vo_num is None:
        return
    name = vo_names.get(vo_num) or f"Virt Op {vo_num}"
    g30_el.set("value", f"= {name} (VO{vo_num})")


def _normalize_enum_display(display: str) -> str:
    """Collapse XML enum labels like 'SRC 1 (SRC 1)' to the URS form 'SRC 1'."""
    if " (" not in display:
        return display
    left, _, right = display.partition(" (")
    if right.rstrip(")") == left:
        return left
    return display


def _format_urs_value(
    attrs: dict[str, str],
    setting_type: str,
    operand_map: dict[str, str],
    *,
    source_urs_value: str = "",
    label_id: str = "",
    signal_codes: Optional[set[str]] = None,
) -> str:
    """Format a setting's attributes as a URS register value string.

    EnerVista device URS conventions (XML-import and device readback):
      * FlexAnalog picks from EnumType 10013 store bare ``code + 0x40000``.
      * FlexLogic ENTRY and other Flex keep ``code (display)``; site-specific
        VO / contact names from the G30 source are preserved when present.
      * OFF is ``0 (OFF)``. Empty FlexLogic END stays bare ``2097152``.
    """
    signal_codes = signal_codes or set()

    if setting_type == "Enum":
        enum_val = attrs.get("EnumValue", "")
        display = _normalize_enum_display(attrs.get("value", ""))
        src_code, src_display = _parse_coded_urs_value(source_urs_value)
        if not display and src_code == enum_val and src_display:
            display = src_display
        if enum_val != "":
            return f"{enum_val} ({display})" if display else enum_val

    if setting_type == "Flex":
        flex_val = attrs.get("FlexValue", "")
        if flex_val == "":
            return setting_attrs_to_urs_value(attrs, setting_type)

        # Signal analog operands: URS stores enum_code + 0x40000 as bare number.
        # OFF (0) stays annotated as ``0 (OFF)``.
        if flex_val in signal_codes and flex_val != "0":
            try:
                return str(int(flex_val) + _G60_USER_DISPLAY_CODE_OFFSET)
            except ValueError:
                pass

        # Assign-virtual-output FlexLogic writes (= VOn) are stored bare in
        # EnerVista XML-imported URS files; keep sense/read operands annotated.
        try:
            fv_int = int(flex_val)
        except ValueError:
            fv_int = -1
        is_assign_vo = (
            _G60_ASSIGN_VO_BASE <= fv_int < _G60_ASSIGN_VO_BASE + 256
            or (attrs.get("value", "") or "").startswith("= ")
            or (source_urs_value or "").lstrip().startswith("= ")
            or " (= " in (source_urs_value or "")
        )
        if is_assign_vo and "FLEXLOGIC_ENTRY" in (label_id or ""):
            return flex_val

        # Empty FlexLogic END token is stored bare in blank Base.urs templates.
        if flex_val == "2097152" and "FLEXLOGIC_ENTRY" in (label_id or ""):
            src = (source_urs_value or "").strip()
            if not src or src in ("2097152", "END"):
                return flex_val

        src_code, src_display = _parse_coded_urs_value(source_urs_value)
        canonical = _flex_code_to_name(operand_map).get(flex_val)
        # Prefer the G30 site-specific display name (custom VO / contact labels)
        # when the source carried one — matches the XML→EnerVista URS style.
        # Exception: FlexValue 0 is always OFF on G60 (G30 sometimes says Disabled).
        if flex_val == "0":
            display = "OFF"
        else:
            display = src_display or attrs.get("value", "") or canonical or ""
        if display:
            return f"{flex_val} ({display})"
        return flex_val

    return setting_attrs_to_urs_value(attrs, setting_type)


def convert_urs(
    g30_urs_path: Path,
    g60_urs_template_path: Path,
    g60_xml_path: Path,
    output_dir: Path,
    *,
    g30_xml_path: Optional[Path] = None,
    app_dir: Optional[Path] = None,
) -> UrsConvertResult:
    """
    Convert a G30 .urs file to G60 format.

    Returns the written output path and a post-write verification report.
    """
    root_dir = app_dir or app_root_dir()

    print(f"Reading G30 URS  : {g30_urs_path}")
    g30_urs = parse_urs_file(g30_urs_path)
    g30_lookup = g30_urs.data_lookup()

    print(f"Reading G60 URS  : {g60_urs_template_path}")
    g60_urs_tpl = parse_urs_file(g60_urs_template_path)

    print(f"Reading G60 XML  : {g60_xml_path}")
    g60_root = parse_xml(g60_xml_path)
    g60_work = copy.deepcopy(g60_root)

    g60_lookup = build_lookup(g60_work)
    g60_flex_fv_map: dict[str, str] = {}
    for s in g60_work.iter("Setting"):
        if s.get("SettingType") == "Flex":
            op = s.get("value", "")
            fv = s.get("FlexValue", "")
            if op and fv and op not in g60_flex_fv_map:
                g60_flex_fv_map[op] = fv

    g60_operand_map = build_flex_operand_map(g60_work)

    companion = g30_xml_path or find_companion_xml(g30_urs_path)
    g60_order_code = g60_work.get("orderCode", g60_urs_tpl.order_code)
    g60_version = g60_work.get("version", g60_urs_tpl.firmware_version)
    g30_version = g30_urs.firmware_version
    if companion and companion.exists():
        g30_version = parse_xml(companion).get("version", g30_version) or g30_version

    g30_signal_code_to_name, signal_source = load_g30_signal_code_to_name(
        g30_urs,
        g60_work,
        root_dir,
        companion_xml=companion if companion and companion.exists() else None,
        g30_version=g30_version or "",
        g60_version=g60_version or "",
    )
    g60_signal_code_to_name, g60_signal_name_to_code = build_signal_operand_tables(g60_work)
    g60_signal_codes = set(g60_signal_code_to_name.keys())
    if g30_signal_code_to_name:
        print(
            f"  User display map : {len(g30_signal_code_to_name):,} G30 codes "
            f"via {signal_source}"
        )
        if g30_version:
            print(f"  G30 firmware     : {g30_version}")
    else:
        print(
            "  Warning: G30 user-display signal map unavailable; "
            "place AnalogoperandTo61850_*.csv under firmware/g30 and firmware/g60",
            file=sys.stderr,
        )

    fw_dir = firmware_dir(root_dir)
    _g30_csv, g30_expected, g30_exact = resolve_analogoperand_csv_detailed(
        fw_dir / "g30", preferred_fw=g30_version or ""
    )
    _g60_csv, g60_expected, g60_exact = resolve_analogoperand_csv_detailed(
        fw_dir / "g60", preferred_fw=g60_version or ""
    )
    if g30_version and not g30_exact:
        print(
            f"  Warning: missing firmware/g30/AnalogoperandTo61850_{g30_expected}.csv "
            f"for G30 FW {g30_version}; using {_g30_csv.name if _g30_csv else 'none'}",
            file=sys.stderr,
        )
    if g60_version and not g60_exact:
        print(
            f"  Warning: missing firmware/g60/AnalogoperandTo61850_{g60_expected}.csv "
            f"for G60 FW {g60_version}; using {_g60_csv.name if _g60_csv else 'none'}",
            file=sys.stderr,
        )

    if companion and companion.exists():
        g30_device_raw = parse_xml(companion).get("deviceName", g30_urs.order_code)
    else:
        # Without a companion XML, derive identity from the .urs filename.
        g30_device_raw = derive_g30_identity_from_urs_stem(g30_urs_path.stem)

    output_device_name = ur_title_case(
        derive_output_device_name(g30_device_raw, g60_order_code)
    )
    output_stem = derive_output_file_stem(output_device_name, g60_version)
    output_path = output_dir / f"{output_stem}.urs"

    print(f"Output device    : {output_device_name}")
    print(f"Output firmware  : {g60_version or '(unknown)'}")
    print(f"Output URS       : {output_path.name}")
    print()

    stats = UrsConversionStats()
    output_rows: list[UrsDataRow] = []
    transferred_keys: set[tuple[str, str, str, str]] = set()
    vo_names = _g30_virtual_output_names(g30_urs)

    for tpl_row in g60_urs_tpl.data_rows:
        key = tpl_row.key
        g60_key = (key[0], key[1], key[2], key[3], "0")
        g60_setting = g60_lookup.get(g60_key)
        g30_row = g30_lookup.get(key)

        if g60_setting is None or g30_row is None:
            # No G30 value to transfer: keep the G60 URS template's native
            # default verbatim.  The template already stores every register in
            # the relay's on-wire format (e.g. IP addresses as packed 32-bit
            # integers), so we must NOT re-derive it from the XML default, which
            # is a human-readable dotted string and corrupts the URS value.
            output_rows.append(tpl_row)
            stats.kept_default += 1
            continue

        setting_type = g60_setting.get("SettingType", "")
        label_id = g60_setting.get("labelID", "") or key[0]

        # Match the XML pipeline's Number rules on the URS path:
        #   * IPv4 registers stay packed uint32 (URS native form)
        #   * other Numbers get G60 decimal precision via reformat_number_value
        #   * Flex + user-display Items still need code remapping
        # Enum / Text / etc. transfer verbatim (already native).
        if setting_type == "Number" and label_id != _USER_DISPLAY_ITEMS_LABEL:
            if is_ipv4_setting(label_id, tpl_row.value, g30_row.value):
                packed = format_ipv4_urs_value(g30_row.value)
                new_value = packed if packed is not None else g30_row.value
            else:
                # Build a transient Setting so maybe_adjust_legacy_number_value
                # can see labelID (IEC PF threshold etc.).
                work = copy.deepcopy(g60_setting)
                adjusted, note = maybe_adjust_legacy_number_value(work, g30_row.value)
                source_num = adjusted if adjusted is not None else g30_row.value
                if note:
                    stats.auto_adjusted += 1
                # Prefer the URS template's native format string for precision
                # (same quantity style EnerVista stores), falling back to XML.
                precision_template = tpl_row.value or work.get("value", "")
                new_value = reformat_number_value(source_num, precision_template)

            output_rows.append(
                UrsDataRow(
                    label_id=tpl_row.label_id,
                    reg_num=tpl_row.reg_num,
                    group=tpl_row.group,
                    module=tpl_row.module,
                    item=tpl_row.item,
                    value=new_value,
                )
            )
            stats.transferred += 1
            transferred_keys.add(key)
            continue

        needs_remap = setting_type == "Flex" or (
            setting_type == "Number" and label_id == _USER_DISPLAY_ITEMS_LABEL
        )

        if not needs_remap:
            output_rows.append(
                UrsDataRow(
                    label_id=tpl_row.label_id,
                    reg_num=tpl_row.reg_num,
                    group=tpl_row.group,
                    module=tpl_row.module,
                    item=tpl_row.item,
                    value=g30_row.value,
                )
            )
            stats.transferred += 1
            transferred_keys.add(key)
            continue

        g30_el = make_setting_element(
            key[0], key[1], key[2], key[3], "0", setting_type, g30_row.value
        )
        if setting_type == "Flex" and "FLEXLOGIC_ENTRY" in label_id:
            _decorate_bare_assign_vo(g30_el, vo_names)

        # Work on a copy so repeated keys in the lookup are not mutated.
        work_setting = copy.deepcopy(g60_setting)

        note = transfer_value(
            work_setting,
            g30_el,
            g60_flex_fv_map,
            g60_operand_map,
            g30_signal_code_to_name or None,
            g60_signal_name_to_code or None,
        )
        if note:
            stats.auto_adjusted += 1

        new_value = _format_urs_value(
            dict(work_setting.attrib),
            setting_type,
            g60_operand_map,
            source_urs_value=g30_row.value,
            label_id=label_id,
            signal_codes=g60_signal_codes,
        )
        output_rows.append(
            UrsDataRow(
                label_id=tpl_row.label_id,
                reg_num=tpl_row.reg_num,
                group=tpl_row.group,
                module=tpl_row.module,
                item=tpl_row.item,
                value=new_value,
            )
        )
        stats.transferred += 1
        transferred_keys.add(key)

    g60_keys = {row.key for row in g60_urs_tpl.data_rows}
    stats.dropped_g30 = sum(1 for key in g30_lookup if key not in g60_keys)

    output_urs = UrsFile(
        header_fields=update_header_for_output(
            g60_urs_tpl.header_fields,
            g60_order_code,
            g60_version,
        ),
        urpc_lines=list(g60_urs_tpl.urpc_lines),
        data_rows=output_rows,
        tail_lines=list(g60_urs_tpl.tail_lines),
    )

    input_paths = {g30_urs_path.resolve(), g60_urs_template_path.resolve(), g60_xml_path.resolve()}
    if output_path.resolve() in input_paths:
        raise ValueError(
            f"Output path '{output_path}' would overwrite an input file. "
            "Pass a different output directory."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_urs_file(output_urs, output_path)

    verification = verify_urs_rows(
        output_rows,
        output_path,
        transferred_keys=transferred_keys,
    )

    sep = "-" * 60
    print(f"\n{sep}")
    print(f"  G30 DATA rows (source)          : {len(g30_urs.data_rows)}")
    print(f"  G60 DATA rows (template)        : {len(g60_urs_tpl.data_rows)}")
    print(f"  Transferred G30 -> G60          : {stats.transferred}")
    if stats.auto_adjusted:
        print(f"    of which auto-adjusted        : {stats.auto_adjusted}")
    print(f"  Kept at G60 template defaults   : {stats.kept_default}")
    print(f"  G30-only (not in G60 template)    : {stats.dropped_g30}")
    print(f"{sep}\n")
    print(f"URS written : {output_path.name}  ({output_path.stat().st_size:,} bytes)")
    print(f"  Post-write verification : {verification.format_summary()}")

    return UrsConvertResult(output_path=output_path, verification=verification)


def main(argv: Optional[list[str]] = None) -> int:
    here = Path(__file__).parent
    args = list(argv if argv is not None else sys.argv[1:])

    g60_urs_path: Optional[Path] = None
    g60_xml_path: Optional[Path] = None
    firmware: Optional[str] = None
    positional: list[str] = []

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--g60-urs" and i + 1 < len(args):
            g60_urs_path = Path(args[i + 1])
            i += 2
            continue
        if arg == "--g60-xml" and i + 1 < len(args):
            g60_xml_path = Path(args[i + 1])
            i += 2
            continue
        if arg == "--firmware" and i + 1 < len(args):
            firmware = args[i + 1]
            i += 2
            continue
        if arg in ("-h", "--help"):
            print(__doc__)
            print("  Default G60 XML : bases/G60 Base.xml  (firmware 860)")
            print("  Paired URS      : same stem as XML, e.g. bases/G60 Base.urs")
            print(f"  Default output  : {here / 'Converted'}")
            return 0
        positional.append(arg)
        i += 1

    if not positional:
        print(__doc__)
        return 0

    g30_path = Path(positional[0])
    output_dir = Path(positional[1]) if len(positional) > 1 else here / "Converted"

    if not g30_path.exists():
        print(f"ERROR: File not found: {g30_path}", file=sys.stderr)
        return 1

    if g30_path.suffix.lower() != ".urs":
        print(f"ERROR: Expected a .urs file, got: {g30_path}", file=sys.stderr)
        return 1

    try:
        g60_xml, g60_urs_template = resolve_g60_templates(
            here,
            firmware=firmware,
            g60_xml_path=g60_xml_path,
            g60_urs_path=g60_urs_path,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    convert_urs(g30_path, g60_urs_template, g60_xml, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
