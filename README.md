# G30 to G60 Relay Settings Converter

Converts GE Multilin UR series **G30** relay settings XML files to **G60** format, producing a ready-to-import G60 XML and a detailed HTML conversion report.

---

## Files

| File | Purpose |
|------|---------|
| `convert_g30_to_g60.py` | Main conversion script |
| `Convert G30 to G60.bat` | Drag-and-drop launcher |
| `G60 Conversion_Publix_850_Base.xml` | G60 template (do not modify or move) |
| `Converted\` | Output folder (created automatically) |

---

## Quick Start — Drag and Drop

1. Locate the G30 settings XML file you want to convert.
2. Drag it onto **`Convert G30 to G60.bat`**.
3. A console window will open, show progress, and close after you press a key.
4. Two output files appear in the `Converted\` subfolder:
   - `<DeviceName>.xml` — the converted G60 settings file
   - `<DeviceName>_OR.html` — the conversion report (open in any browser)

---

## Command-Line Usage

```
python convert_g30_to_g60.py  <g30_source.xml>  [output_dir]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `g30_source.xml` | Yes | Path to the G30 settings file to convert |
| `output_dir` | No | Folder for outputs (default: `Converted\` next to the script) |

**Examples:**

```batch
:: Basic — outputs go to .\Converted\
python convert_g30_to_g60.py "Publix Firmware7-6_208V4000A[86].xml"

:: Custom output directory
python convert_g30_to_g60.py "source.xml" "C:\Work\Converted"
```

---

## Requirements

- **Python 3.10+** — standard library only, no pip installs needed
- The script and `G60 Conversion_Publix_850_Base.xml` must remain in the **same folder**
- G30 and G60 source files can be **UTF-8 or UTF-16 LE** encoded (the script auto-detects)

---

## How the Conversion Works

### 1. Parsing

Both the G30 source file and the G60 template are read as raw bytes. The script first attempts to parse as UTF-8 (allowing for XML encoding declarations), falling back to UTF-16 LE if needed. The G60 template provides the output structure; its `version` and `orderCode` attributes are always preserved unchanged.

### 2. Setting Matching

Every setting in the G60 template is matched against the G30 source using a five-part composite key:

```
(labelID, group, module, item, bit)
```

This uniquely identifies each register across both devices, regardless of firmware differences in display names or section layout. For example, two breaker modules that share the same `labelID` are distinguished by their `module` attribute.

### 3. Value Transfer Rules

| Setting Type | What is transferred | What is kept from G60 |
|---|---|---|
| **Number** | Numeric value, reformatted to match G60 decimal precision | `MinValue`, `MaxValue`, `Unit` |
| **Enum** | `value` (display text) + `EnumValue` (selection index) | `EnumFormatIndex` (G60 firmware's enum table) |
| **Flex** | Operand name string; FlexValue resolved to G60 firmware code | G60 FlexValue when G30 operand is hardware-unavailable |

**Number precision reformatting:** UR Setup rejects Number values whose decimal format doesn't match the register's expected precision. For example, if the G30 stored `1.00 Hz` but the G60 register expects `1.000 Hz`, the script reformats the value automatically. The numeric quantity is preserved; only the number of decimal places changes.

**Flex FlexValue firmware codes:** The `FlexValue` attribute in Flex settings is a firmware-internal operand identifier. These codes differ between G30 7.x and G60 8.x firmware for protection element outputs (`OVERFREQ 1 OP`, `DIR POWER 1 STG1 OP`, etc.) and system operands (`SETTING GROUP ACT 1`, etc.). The script builds a name-to-code lookup from the G60 template and substitutes the correct G60 code for every transferred Flex setting.

**Hardware-unavailable operands:** If a G30 Flex setting references a measurement signal that doesn't exist in the G60 hardware configuration (for example, `SRC4 Ia RMS` on a G60 that has no Source 4), the setting reverts to the G60 template's default (`OFF`) rather than writing an invalid code.

### 4. Unmatched Settings

| Case | Result |
|------|--------|
| G30 setting has a match in G60 | Value transferred (with rules above) |
| G30 setting has **no match** in G60 | Dropped — logged in HTML report |
| G60 setting has **no match** in G30 | Kept at G60 template default — logged in HTML report |

### 5. Output Naming

The output filename is derived automatically:

1. **Site prefix** — first word of the G30 `deviceName` (e.g. `publix`)
2. **G60 model** — first segment of the G60 `orderCode`, lowercased (e.g. `G60-V00-…` → `g60`)
3. **Specs suffix** — everything from the first `_` in the G30 `deviceName` onwards (e.g. `_208v4000a[86]`)
4. **Combined raw name** — `{prefix} {model}{suffix}` (e.g. `publix g60_208v4000a[86]`)
5. **UR Setup title-casing** applied:
   - Capitalize the first letter of each space-delimited token
   - Capitalize any letter that immediately follows a digit
   - Result: `Publix G60_208V4000A[86]`

The output XML's `deviceName` attribute is updated to match. The HTML report shares the same base name with `_OR` appended.

---

## Output Files

### Converted XML (`<DeviceName>.xml`)

A valid G60 settings file ready to import into GE UR Setup. Encoded as UTF-16 LE to match UR Setup's expectations. The G60 `version` and `orderCode` from the template are always preserved on line 2.

### Conversion Report (`<DeviceName>_OR.html`)

A self-contained HTML file (no external dependencies) with the following sections:

| Section | Contents |
|---------|----------|
| **Value Changes** | Settings where the G30 value differed from the G60 template default. Shows both the template value and the applied G30 value side by side. |
| **Setting Name Differences** | Settings matched by key where the display name differs between G30 and G60 firmware. Common with contact input labels and renamed virtual outputs. |
| **Range Warnings** | Number settings where the transferred G30 value falls outside the G60 register's `MinValue`/`MaxValue` bounds. These are applied but flagged for review. |
| **G60-Only Settings** | Settings present in the G60 template but absent in the G30 source. These retain the G60 template default. Typically G60-exclusive features. |
| **Dropped G30 Settings** | Settings from the G30 that have no matching register in the G60. These values are not carried over. |
| **Transferred Unchanged** | Settings that matched and had identical values in both files. Collapsed by default. |

All tables support live text filtering. The report can be printed directly from the browser.

---

## Important Notes

### Template File

The file `G60 Conversion_Publix_850_Base.xml` is the conversion base. It defines:
- The complete G60 register structure
- The correct `version` (`850`) and `orderCode` (`G60-V00-HCL-F8L-H6P-M8L-P5A-UXX-WXX`) that appear in every converted file
- Default values for G60-only settings
- The G60 firmware's FlexValue operand codes used to correct Flex settings on transfer

**Do not modify, rename, or move this file.** If you need to update the template (e.g. for a different G60 order code), update the `G60_TEMPLATE` constant near the bottom of `convert_g30_to_g60.py`:

```python
G60_TEMPLATE = here / "G60 Conversion_Publix_850_Base.xml"
```

### What the Script Does Not Change

- G60 `version` and `orderCode` — always from the template
- `EnumFormatIndex` — always from the G60 template (G30 and G60 use different enum tables)
- `MinValue`, `MaxValue`, `Unit` on Number settings — always from the G60 template

### Expected Differences After Import into UR Setup

When comparing the converted file against an expert-configured G60 reference in UR Setup's Device Comparison Report, some **Differences** are expected and intentional:

- **Contact input name cascade** — If G30 named a contact `Gen Aux` and the reference G60 named it `Gen Aux On`, FlexLogic entries that reference that contact will differ (`Gen Aux On(H8a)` vs `Gen Aux On On(H8a)`). This is correct behavior; the conversion preserves the G30 contact names.
- **G60-only settings** — Features that exist only in the G60 (e.g. SENS DIR POWER) will appear as "Missing Settings" since they were not present in the G30 to configure.
- **Intentional configuration differences** — Any setting where the expert G60 reference was deliberately configured differently from the G30 source will appear as a Difference.

**Zero Invalid Settings** is the target after a clean conversion. Invalid Settings indicate a value format or firmware-code problem that requires investigation.

---

## Recent Changes

- **2026-04-23**: Updated XML parsing to auto-detect encoding (UTF-8 first, then UTF-16 LE fallback). Added control character sanitization to handle G30 files with invalid characters in setting values or attributes.
- **2026-04-23**: Improved numeric range detection to parse lower-firmware G30 number values consistently, reducing missed out-of-range warnings.
- **2026-04-23**: Added automatic legacy scaling for IEC power factor threshold values from lower-firmware G30 files, with explicit reporting of the adjustment.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ERROR: File not found` | Wrong path or filename typo | Check the path passed to the script |
| `ERROR: Output path would overwrite an input file` | Output directory is the same folder as the template | Use the default `Converted\` folder or pass a different output path |
| Script opens and immediately closes | Python not on system PATH | Run from a terminal with `py convert_g30_to_g60.py ...` |
| UR Setup reports Invalid Settings after import | Flex operand from G30 not resolvable in G60 | Check the HTML report's Value Changes section; the affected setting may need manual adjustment in UR Setup |
| Output filename looks wrong | G30 `deviceName` doesn't follow the expected `prefix_specs` pattern | The raw and derived device names are printed to the console; verify the G30 source file's `deviceName` attribute on line 2 |
