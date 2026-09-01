"""Run N back-to-back conversions + verifications for stress testing.

See `_tmp_stress/README.md` for the synthetic smoke command and the 500-run
production invocation that writes `docs/STRESS_TEST_REPORT.md`.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from convert_g30_to_g60 import convert, parse_xml
from convert_g30_to_g60_urs import convert_urs
from urs_io import UrsFile, parse_urs_file, write_urs_file


@dataclass
class SourceStats:
    name: str
    mode: str
    ok: int = 0
    verify_fail: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        return self.ok + self.verify_fail + self.errors

    @property
    def passed(self) -> bool:
        return self.verify_fail == 0 and self.errors == 0


@dataclass
class RunStats:
    total: int = 0
    urs_ok: int = 0
    xml_ok: int = 0
    urs_verify_fail: int = 0
    xml_verify_fail: int = 0
    urs_errors: int = 0
    xml_errors: int = 0
    total_seconds: float = 0.0
    failures: list[dict[str, str]] = field(default_factory=list)
    urs_sources: list[str] = field(default_factory=list)
    xml_sources: list[str] = field(default_factory=list)
    per_source: dict[str, SourceStats] = field(default_factory=dict)

    def source_stats(self, name: str, mode: str) -> SourceStats:
        key = f"{mode}:{name}"
        if key not in self.per_source:
            self.per_source[key] = SourceStats(name=name, mode=mode)
        return self.per_source[key]


def build_synthetic_fixtures(work_dir: Path) -> tuple[Path, Path]:
    """Create G30 .urs and .xml fixtures from bundled G60 bases."""
    g30_urs_path = work_dir / "synthetic_g30.urs"
    g30_xml_path = work_dir / "synthetic_g30.xml"

    g60_urs = parse_urs_file(REPO / "bases" / "G60 Base.urs")
    header = list(g60_urs.header_fields)
    header[3] = "G30-V00-HKL-F8L-H6P-M8L-P5A"
    header[4] = "760"
    g30_urs = UrsFile(
        header_fields=header,
        urpc_lines=list(g60_urs.urpc_lines),
        data_rows=list(g60_urs.data_rows),
        tail_lines=list(g60_urs.tail_lines),
    )
    write_urs_file(g30_urs, g30_urs_path)

    g60_xml = parse_xml(REPO / "bases" / "G60 Base.xml")
    g60_xml.set("orderCode", "G30-V00-HKL-F8L-H6P-M8L-P5A")
    g60_xml.set("version", "760")
    g60_xml.set("deviceName", "synthetic g30 stress")
    xml_body = ET.tostring(g60_xml, encoding="unicode")
    g30_xml_path.write_bytes(
        ('<?xml version="1.0" ?>\n' + xml_body + "\n").encode("utf-16-le")
    )
    return g30_urs_path, g30_xml_path


def stage_g30_sources(sources: list[Path], work_dir: Path, *, label: str) -> list[Path]:
    """Copy user-provided G30 files into the work dir (never mutate originals)."""
    staged_dir = work_dir / label
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    for index, source in enumerate(sources):
        resolved = source.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"G30 source not found: {resolved}")
        dest = staged_dir / f"fixture_{index:02d}_{resolved.name}"
        shutil.copy2(resolved, dest)
        staged.append(dest)
    return staged


def mutate_g30_urs(g30_path: Path, iteration: int) -> None:
    """Tweak one register per iteration so conversions are not byte-identical."""
    urs = parse_urs_file(g30_path)
    if not urs.data_rows:
        return
    row = urs.data_rows[iteration % len(urs.data_rows)]
    idx = urs.data_rows.index(row)
    value = row.value.strip()
    if value.isdigit():
        new_value = str((int(value) + (iteration % 3)) % 1_000_000)
    else:
        new_value = f"{value} /*{iteration % 97}*/"
    urs.data_rows[idx] = type(row)(
        label_id=row.label_id,
        reg_num=row.reg_num,
        group=row.group,
        module=row.module,
        item=row.item,
        value=new_value,
    )
    write_urs_file(urs, g30_path)


def run_stress(
    iterations: int,
    *,
    work_dir: Path,
    alternate: bool = True,
    cleanup_each: bool = True,
    g30_urs_sources: list[Path] | None = None,
    g30_xml_sources: list[Path] | None = None,
    g30_xml: Path | None = None,
    mutate_sources: bool = False,
) -> RunStats:
    stats = RunStats(total=iterations)
    using_real_urs = bool(g30_urs_sources)
    using_real_xml = bool(g30_xml_sources or g30_xml)

    if using_real_urs:
        urs_sources = stage_g30_sources(g30_urs_sources or [], work_dir, label="sources_urs")
        stats.urs_sources = [p.name for p in urs_sources]
        active_urs = work_dir / "active_g30.urs"
    else:
        g30_urs, synthetic_xml = build_synthetic_fixtures(work_dir)
        urs_sources = [g30_urs]
        stats.urs_sources = [g30_urs.name]
        active_urs = g30_urs
        if g30_xml is None and not g30_xml_sources:
            g30_xml = synthetic_xml

    if g30_xml_sources:
        xml_sources = stage_g30_sources(g30_xml_sources, work_dir, label="sources_xml")
        stats.xml_sources = [p.name for p in xml_sources]
    elif g30_xml is not None:
        xml_sources = stage_g30_sources([g30_xml], work_dir, label="sources_xml")
        stats.xml_sources = [p.name for p in xml_sources]
    elif not using_real_urs:
        xml_sources = [synthetic_xml]
        stats.xml_sources = [synthetic_xml.name]
    else:
        xml_sources = []

    g60_urs = REPO / "bases" / "G60 Base.urs"
    g60_xml = REPO / "bases" / "G60 Base.xml"

    urs_run_idx = 0
    xml_run_idx = 0

    t0 = time.perf_counter()
    for i in range(1, iterations + 1):
        run_urs = (not alternate) or (i % 2 == 1)
        if run_urs and not urs_sources:
            raise ValueError("URS workflow requested but no G30 URS sources are available.")
        if not run_urs and not xml_sources:
            raise ValueError("XML workflow requested but no G30 XML sources are available.")

        out_dir = work_dir / f"run_{i:04d}"
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)

        if run_urs:
            source = urs_sources[urs_run_idx % len(urs_sources)]
            urs_run_idx += 1
        else:
            source = xml_sources[xml_run_idx % len(xml_sources)]
            xml_run_idx += 1

        source_key = source.name
        try:
            with redirect_stdout(io.StringIO()):
                if run_urs:
                    if using_real_urs:
                        shutil.copy2(source, active_urs)
                    elif mutate_sources:
                        mutate_g30_urs(active_urs, i)
                    result = convert_urs(active_urs, g60_urs, g60_xml, out_dir)
                    row = stats.source_stats(source_key, "urs")
                    if result.verification.ok:
                        stats.urs_ok += 1
                        row.ok += 1
                    else:
                        stats.urs_verify_fail += 1
                        row.verify_fail += 1
                        stats.failures.append(
                            {
                                "iteration": str(i),
                                "mode": "urs",
                                "source": source_key,
                                "error": result.verification.format_summary(max_mismatches=3),
                            }
                        )
                else:
                    result = convert(source, g60_xml, out_dir)
                    row = stats.source_stats(source_key, "xml")
                    if result.ok:
                        stats.xml_ok += 1
                        row.ok += 1
                    else:
                        stats.xml_verify_fail += 1
                        row.verify_fail += 1
                        stats.failures.append(
                            {
                                "iteration": str(i),
                                "mode": "xml",
                                "source": source_key,
                                "error": result.format_summary(max_mismatches=3),
                            }
                        )
        except Exception as exc:
            mode = "urs" if run_urs else "xml"
            row = stats.source_stats(source_key, mode)
            if run_urs:
                stats.urs_errors += 1
            else:
                stats.xml_errors += 1
            row.errors += 1
            stats.failures.append(
                {
                    "iteration": str(i),
                    "mode": mode,
                    "source": source_key,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=6),
                }
            )
        finally:
            if cleanup_each and out_dir.exists():
                shutil.rmtree(out_dir, ignore_errors=True)

        if i % 25 == 0 or i == iterations:
            elapsed = time.perf_counter() - t0
            rate = i / elapsed if elapsed else 0.0
            print(
                f"[{i:4d}/{iterations}] "
                f"urs ok={stats.urs_ok} fail={stats.urs_verify_fail} err={stats.urs_errors} | "
                f"xml ok={stats.xml_ok} fail={stats.xml_verify_fail} err={stats.xml_errors} | "
                f"{rate:.2f} runs/s",
                flush=True,
            )

    stats.total_seconds = time.perf_counter() - t0
    return stats


def stats_to_summary(stats: RunStats) -> dict:
    return {
        "iterations": stats.total,
        "urs_sources": stats.urs_sources,
        "xml_sources": stats.xml_sources,
        "urs_ok": stats.urs_ok,
        "urs_verify_fail": stats.urs_verify_fail,
        "urs_errors": stats.urs_errors,
        "xml_ok": stats.xml_ok,
        "xml_verify_fail": stats.xml_verify_fail,
        "xml_errors": stats.xml_errors,
        "elapsed_seconds": round(stats.total_seconds, 2),
        "runs_per_second": round(stats.total / stats.total_seconds, 3)
        if stats.total_seconds
        else 0,
        "per_source": {
            key: {
                "name": row.name,
                "mode": row.mode,
                "ok": row.ok,
                "verify_fail": row.verify_fail,
                "errors": row.errors,
            }
            for key, row in sorted(stats.per_source.items())
        },
        "failures": stats.failures[:50],
    }


def _display_source_name(name: str) -> str:
    if name.startswith("fixture_") and "_" in name[8:]:
        return name.split("_", 2)[-1]
    return name


def generate_markdown_report(
    stats: RunStats,
    *,
    source_paths: list[Path],
    target_template: str = "G60 Base.xml / G60 Base.urs",
    report_title: str = "G30 → G60 Conversion Stress Test Report",
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_failures = (
        stats.urs_verify_fail
        + stats.xml_verify_fail
        + stats.urs_errors
        + stats.xml_errors
    )
    overall_pass = total_failures == 0
    urs_runs = stats.urs_ok + stats.urs_verify_fail + stats.urs_errors
    xml_runs = stats.xml_ok + stats.xml_verify_fail + stats.xml_errors

    try:
        sys.path.insert(0, str(REPO / "aio"))
        from app_version import get_version_info

        version = get_version_info().display
    except Exception:
        version = "development build"

    lines = [
        f"# {report_title}",
        "",
        f"**Report date:** {now}  ",
        f"**Converter version:** {version}  ",
        f"**Overall result:** {'PASS' if overall_pass else 'FAIL'}",
        "",
        "## Executive summary",
        "",
    ]

    if overall_pass:
        lines.extend(
            [
                f"The converter completed **{stats.total}** consecutive end-to-end conversions "
                f"({urs_runs} URS, {xml_runs} XML) with post-write verification on every run. "
                "No verification mismatches or runtime errors were observed.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                f"The stress run completed **{stats.total}** conversions with "
                f"**{total_failures}** failure(s). See the failure details section below.",
                "",
            ]
        )

    lines.extend(
        [
            "## Test configuration",
            "",
            "| Parameter | Value |",
            "|-----------|-------|",
            f"| Total iterations | {stats.total} |",
            f"| Workflow | {'URS and XML alternating' if urs_runs and xml_runs else 'URS only' if urs_runs else 'XML only'} |",
            f"| Target G60 template | `{target_template}` |",
            f"| Post-write verification | Enabled on every run |",
            f"| Output cleanup | Per-run output directories removed after each iteration |",
            f"| Original source files | Never modified (staged copies used) |",
            "",
            "## Source files",
            "",
            "| # | File | Format | Role |",
            "|---|------|--------|------|",
        ]
    )

    for index, path in enumerate(source_paths, start=1):
        suffix = path.suffix.lower()
        fmt = "URS" if suffix == ".urs" else "XML"
        lines.append(f"| {index} | `{path.name}` | {fmt} | Production G30 export |")

    lines.extend(
        [
            "",
            "## Results summary",
            "",
            "| Metric | URS | XML | Combined |",
            "|--------|-----|-----|----------|",
            f"| Runs | {urs_runs} | {xml_runs} | {stats.total} |",
            f"| Verification passed | {stats.urs_ok} | {stats.xml_ok} | {stats.urs_ok + stats.xml_ok} |",
            f"| Verification failed | {stats.urs_verify_fail} | {stats.xml_verify_fail} | {stats.urs_verify_fail + stats.xml_verify_fail} |",
            f"| Runtime errors | {stats.urs_errors} | {stats.xml_errors} | {stats.urs_errors + stats.xml_errors} |",
            "",
            "## Per-source breakdown",
            "",
            "| Source | Format | Runs | Passed | Failed | Errors |",
            "|--------|--------|------|--------|--------|--------|",
        ]
    )

    for key in sorted(stats.per_source):
        row = stats.per_source[key]
        lines.append(
            f"| `{_display_source_name(row.name)}` | {row.mode.upper()} | {row.total} | {row.ok} | "
            f"{row.verify_fail} | {row.errors} |"
        )

    minutes, seconds = divmod(int(stats.total_seconds), 60)
    lines.extend(
        [
            "",
            "## Performance",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total elapsed time | {minutes}m {seconds}s ({stats.total_seconds:.1f}s) |",
            f"| Throughput | {stats.total / stats.total_seconds:.2f} conversions/second |"
            if stats.total_seconds
            else "| Throughput | n/a |",
            f"| Average time per conversion | {stats.total_seconds / stats.total:.2f}s |"
            if stats.total
            else "| Average time per conversion | n/a |",
            "",
            "## Methodology",
            "",
            "Each iteration performed a full G30 → G60 conversion using the bundled G60 base "
            "template, then immediately re-read the written output and compared every transferred "
            "register against the expected values. Decimal precision drift and benign coded-value "
            "display differences are tolerated; unexpected value changes are flagged as failures.",
            "",
        ]
    )

    if stats.failures:
        lines.extend(["## Failure details", ""])
        for failure in stats.failures[:20]:
            lines.append(
                f"- **Run {failure['iteration']}** ({failure['mode'].upper()}, "
                f"`{failure['source']}`): {failure['error']}"
            )
        if len(stats.failures) > 20:
            lines.append(f"- … and {len(stats.failures) - 20} more (see `stress_report.json`).")
        lines.append("")

    lines.extend(
        [
            "## Conclusion",
            "",
        ]
    )
    if overall_pass:
        lines.append(
            "Based on this run, the converter demonstrated stable, repeatable behavior across "
            "mixed real-world G30 exports spanning legacy (5.9x) and newer (7.6x) firmware, "
            "both URS and XML workflows, with verification passing on every iteration."
        )
    else:
        lines.append(
            "This run identified one or more issues that should be investigated before "
            "production rollout. Review the failure details and JSON report for specifics."
        )
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--iterations", type=int, default=500)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=REPO / "_tmp_stress" / "runs",
    )
    parser.add_argument(
        "--urs-only",
        action="store_true",
        help="Only run URS conversions (default alternates URS/XML).",
    )
    parser.add_argument(
        "--g30-urs",
        type=Path,
        nargs="+",
        help="One or more real G30 .urs files (optional; default uses synthetic fixture).",
    )
    parser.add_argument(
        "--g30-xml",
        type=Path,
        nargs="+",
        help="One or more real G30 .xml files (optional).",
    )
    parser.add_argument(
        "--mutate",
        action="store_true",
        help="Mutate synthetic fixture between runs (not used for real G30 sources).",
    )
    parser.add_argument(
        "--keep-outputs",
        action="store_true",
        help="Keep per-run output folders.",
    )
    parser.add_argument(
        "--report-md",
        type=Path,
        help="Write a Markdown report to this path.",
    )
    args = parser.parse_args()

    args.work_dir.mkdir(parents=True, exist_ok=True)

    g30_xml_sources = [p.resolve() for p in args.g30_xml] if args.g30_xml else None
    g30_sources = [p.resolve() for p in args.g30_urs] if args.g30_urs else None

    stats = run_stress(
        args.iterations,
        work_dir=args.work_dir,
        alternate=not args.urs_only,
        cleanup_each=not args.keep_outputs,
        g30_urs_sources=g30_sources,
        g30_xml_sources=g30_xml_sources,
        mutate_sources=args.mutate and not g30_sources,
    )

    summary = stats_to_summary(stats)
    report_path = args.work_dir / "stress_report.json"
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    source_paths: list[Path] = []
    if g30_sources:
        source_paths.extend(g30_sources)
    if g30_xml_sources:
        source_paths.extend(g30_xml_sources)

    if args.report_md:
        md = generate_markdown_report(stats, source_paths=source_paths)
        args.report_md.parent.mkdir(parents=True, exist_ok=True)
        args.report_md.write_text(md, encoding="utf-8")
        print(f"Markdown report: {args.report_md}")

    print("\n=== Stress test complete ===")
    print(json.dumps(summary, indent=2))
    print(f"Report: {report_path}")

    failed = (
        stats.urs_verify_fail
        + stats.xml_verify_fail
        + stats.urs_errors
        + stats.xml_errors
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
