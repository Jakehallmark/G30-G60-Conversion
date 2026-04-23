"""
G30 to G60 relay settings converter for GE Multilin UR series.

Produces:
  1. Output XML  - G60 structure with G30 values transferred in
  2. HTML report - same base name as output XML, with _OR suffix

Matching key: (labelID, group, module, item, bit)

Output naming:
  - Reads deviceName and version from the G30 source file.
  - Reads deviceName from the G60 template (carries the G60 model identifier).
  - Replaces the G30 model identifier in the G30 deviceName with the G60 model
    identifier, then applies UR Setup's title-casing convention to produce the
    output filename (e.g. "publix g60_208v4000a[86]" -> "Publix G60_208V4000A[86]").
  - The output XML's deviceName attribute is updated to match.
  - version and orderCode in the output XML are always preserved from the G60 template.

Usage:
  python convert_g30_to_g60.py [g30_input] [g60_template] [output_dir]
  (all optional; defaults to files in the same folder as this script)
"""

import html as html_lib
import re
import xml.etree.ElementTree as ET
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Data structures ────────────────────────────────────────────────────────────

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


# ── XML helpers ────────────────────────────────────────────────────────────────

def read_utf16le(path: Path) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-16-le")


def parse_xml(path: Path) -> ET.Element:
    return ET.fromstring(read_utf16le(path))


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


def derive_output_device_name(g30_device_name: str, g60_order_code: str) -> str:
    """
    Build the output deviceName from the G30 device identity and the G60 model.

    Approach:
      - Site prefix  = first word of G30 deviceName       (e.g. 'publix')
      - G60 model    = first segment of G60 orderCode,
                       lowercased                          (e.g. 'G60' -> 'g60')
      - Specs suffix = everything from the first '_' in
                       the G30 deviceName onwards          (e.g. '_208v4000a[86]')
      - Result       = '{prefix} {model}{suffix}'

    This is robust to any G60 template because it reads the model from the
    orderCode rather than from the template's deviceName.

    Example:
      g30_device_name = 'publix firmware7-6_208v4000a[86]'
      g60_order_code  = 'G60-V00-HCL-F8L-H6P-M8L-P5A-UXX-WXX'
      result          = 'publix g60_208v4000a[86]'
    """
    g60_model = g60_order_code.split("-")[0].lower()
    first_word = g30_device_name.split(" ")[0] if " " in g30_device_name else g30_device_name
    underscore_idx = g30_device_name.find("_")
    suffix = g30_device_name[underscore_idx:] if underscore_idx != -1 else ""
    return f"{first_word} {g60_model}{suffix}"


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


def check_range(setting: ET.Element, g30_value_str: str) -> Optional[str]:
    try:
        numeric = g30_value_str.split()[0]
        val = float(numeric)
        lo = setting.get("MinValue")
        hi = setting.get("MaxValue")
        if lo is not None and val < float(lo):
            return f"{val} < G60 min {lo}"
        if hi is not None and val > float(hi):
            return f"{val} > G60 max {hi}"
    except (ValueError, IndexError, AttributeError):
        pass
    return None


_NUM_PATTERN = re.compile(r"^(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s*(.*)", re.DOTALL)


def reformat_number_value(g30_value: str, g60_template_value: str) -> str:
    """Reformat a G30 Number value to match the decimal precision of the G60 template value.

    UR Setup rejects values whose decimal precision doesn't match the register's
    expected format (e.g. '1.00 Hz' when the G60 register stores '1.000 Hz').
    We keep the G30 numeric quantity but emit it with the G60's decimal places.
    """
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


def transfer_value(g60_el: ET.Element, g30_el: ET.Element) -> None:
    stype = g60_el.get("SettingType", "")
    raw_g30_value = g30_el.get("value", g60_el.get("value", ""))
    if stype == "Number":
        g60_template_value = g60_el.get("value", "")
        g60_el.set("value", reformat_number_value(raw_g30_value, g60_template_value))
    else:
        g60_el.set("value", raw_g30_value)
    if stype == "Enum" and "EnumValue" in g30_el.attrib:
        g60_el.set("EnumValue", g30_el.get("EnumValue", ""))
    elif stype == "Flex" and "FlexValue" in g30_el.attrib:
        g60_el.set("FlexValue", g30_el.get("FlexValue", ""))


# ── Core conversion ────────────────────────────────────────────────────────────

def convert(
    g30_path: Path,
    g60_template_path: Path,
    output_dir: Path,
) -> None:
    print(f"Reading G30      : {g30_path}")
    g30_root = parse_xml(g30_path)
    print(f"Reading G60      : {g60_template_path}")
    g60_root = parse_xml(g60_template_path)

    # ── Derive output device name and file names ───────────────────────────────
    g30_device_name_raw = g30_root.get("deviceName", "")
    g60_device_name_raw = g60_root.get("deviceName", "")

    # Combine: take the G30 project identity, swap in the G60 model from orderCode
    g60_order_code = g60_root.get("orderCode", "")
    output_device_name_raw = derive_output_device_name(g30_device_name_raw, g60_order_code)
    output_device_name = ur_title_case(output_device_name_raw)

    output_xml_path  = output_dir / f"{output_device_name}.xml"
    output_html_path = output_dir / f"{output_device_name}_OR.html"

    print(f"Output device    : {output_device_name}")
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

    # Update output XML's deviceName to the properly-cased derived name
    g60_root.set("deviceName", output_device_name)

    g30_lookup = build_lookup(g30_root)
    g60_path_map = build_path_map(g60_root)
    g30_path_map = build_path_map(g30_root)

    transferred_records: list[TransferredRecord] = []
    g60_only_records: list[G60OnlyRecord] = []

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
            rng_warn = None
            if setting.get("SettingType") == "Number":
                rng_warn = check_range(setting, g30_match.get("value", ""))

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
                g60_template_value=g60_template_value,
                range_warning=rng_warn,
            )
            transferred_records.append(rec)
            transfer_value(setting, g30_match)
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
    unchanged = [r for r in transferred_records if not r.value_changed]

    # ── Console summary ────────────────────────────────────────────────────────
    SEP = "-" * 60
    print(f"\n{SEP}")
    print(f"  Total G30 settings              : {len(g30_lookup)}")
    print(f"  Transferred G30 -> G60          : {len(transferred_records)}")
    print(f"    of which values changed       : {len(value_changes)}")
    print(f"    of which names differ         : {len(name_diffs)}")
    print(f"  G60-only (kept at defaults)     : {len(g60_only_records)}")
    print(f"  G30-only (dropped)              : {len(dropped_records)}")
    if range_warnings:
        print(f"\n  WARNING: {len(range_warnings)} out-of-range value(s) -- review HTML report")
    print(f"{SEP}\n")

    # ── Safety: refuse to overwrite input files ────────────────────────────────
    input_paths = {g30_path.resolve(), g60_template_path.resolve()}
    for out in (output_xml_path, output_html_path):
        if out.resolve() in input_paths:
            print(f"ERROR: Output path '{out}' would overwrite an input file.", file=sys.stderr)
            print("       Pass a different output directory as the third argument.", file=sys.stderr)
            sys.exit(1)

    # ── Write output XML ───────────────────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    ET.indent(g60_root, space="\t")
    xml_body = ET.tostring(g60_root, encoding="unicode")
    xml_out = '<?xml version="1.0" ?>\n' + xml_body + "\n"
    with open(output_xml_path, "wb") as f:
        f.write(xml_out.encode("utf-16-le"))
    print(f"XML  written : {output_xml_path.name}  ({output_xml_path.stat().st_size:,} bytes)")

    # ── Write HTML report ──────────────────────────────────────────────────────
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


# ── HTML report builder ────────────────────────────────────────────────────────

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

    # ── Section: Value changes ─────────────────────────────────────────────────
    vc_rows = []
    for r in sorted(value_changes, key=lambda x: (x.path, x.g60_name)):
        warn = f' <span class="warn-icon" title="{e(r.range_warning)}">&#9888;</span>' if r.range_warning else ""
        name_note = (f'<br><span class="dim">G30 name: {e(r.g30_name)}</span>'
                     if r.name_changed else "")
        vc_rows.append(
            f"<tr>"
            f"{path_cell(r.path)}"
            f"<td>{e(r.g60_name)}{name_note}{warn}</td>"
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

    # ── Section: Name differences ──────────────────────────────────────────────
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

    # ── Section: Range warnings ────────────────────────────────────────────────
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

    # ── Section: G60-only ──────────────────────────────────────────────────────
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

    # ── Section: Dropped ──────────────────────────────────────────────────────
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

    # ── Section: Unchanged transfers ──────────────────────────────────────────
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

    # ── Assemble body sections ─────────────────────────────────────────────────
    range_section = ""
    if range_warnings:
        range_section = section(
            "Range Warnings — Values Outside G60 Valid Range",
            len(range_warnings), "&#9888;", "color-warn", rw_content, "sec-rw"
        )

    sections_html = (
        range_section +
        section("Value Changes — G30 Value Differs from G60 Template Default",
                len(value_changes), "&#8644;", "color-changed", vc_content, "sec-vc") +
        section("Setting Name Differences — Same Register, Different Display Name",
                len(name_diffs), "&#8756;", "color-name", nd_content, "sec-nd") +
        section("G60-Only Settings — New in G60, Kept at Template Defaults",
                len(g60_only), "&#8853;", "color-g60only", go_content, "sec-go") +
        section("Dropped G30 Settings — No Equivalent in G60",
                len(dropped), "&#10007;", "color-dropped", dr_content, "sec-dr") +
        section("Transferred Unchanged — Same Value in Both Files",
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

  /* ── Header ── */
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

  /* ── Summary bar ── */
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

  /* ── Sections / cards ── */
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

  /* ── Search ── */
  .search-box {{ width: 100%; max-width: 400px; padding: 7px 12px; margin-bottom: 12px;
                 border: 1px solid var(--border); border-radius: 6px;
                 font-size: 13px; color: var(--text); background: var(--bg); }}
  .search-box:focus {{ outline: none; border-color: var(--changed); }}

  /* ── Tables ── */
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

  /* ── Cell types ── */
  .path {{ font-size: 12px; color: var(--dim); white-space: nowrap; }}
  .path .sep {{ color: #bbb; margin: 0 2px; }}
  .mono {{ font-family: var(--mono); font-size: 12px; }}
  .small {{ font-size: 11px; }}
  .dim {{ color: var(--dim); font-size: 11px; }}
  .old-val {{ color: var(--dropped); font-family: var(--mono); font-size: 12px; }}
  .new-val {{ color: var(--ok); font-family: var(--mono); font-size: 12px; font-weight: 600; }}
  .warn-val {{ color: var(--warn); font-family: var(--mono); font-size: 12px; font-weight: 600; }}
  .warn-icon {{ color: var(--warn); cursor: help; }}

  /* ── Badges ── */
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


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    here = Path(__file__).parent

    # ── G60 template is fixed — update this path if you move the template file.
    G60_TEMPLATE = here / "G60 Conversion_Publix_850_Base.xml"

    # Drag-and-drop or command-line usage:
    #   python convert_g30_to_g60.py  <g30_source.xml>  [output_dir]
    #
    # When a file is dropped onto this script (or the .bat launcher), Windows
    # passes its path as sys.argv[1].  The G60 template and output directory
    # are resolved automatically — no other arguments are needed.
    if len(sys.argv) < 2:
        print("Usage:  python convert_g30_to_g60.py  <g30_source.xml>  [output_dir]")
        print(f"  G60 template : {G60_TEMPLATE}")
        print(f"  Output dir   : {here / 'Converted'}  (default)")
        sys.exit(0)

    g30_path          = Path(sys.argv[1])
    g60_template_path = G60_TEMPLATE
    output_dir        = Path(sys.argv[2]) if len(sys.argv) > 2 else here / "Converted"

    for p in (g30_path, g60_template_path):
        if not p.exists():
            print(f"ERROR: File not found: {p}", file=sys.stderr)
            sys.exit(1)

    convert(g30_path, g60_template_path, output_dir)
