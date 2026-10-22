"""Command-line interface for vcf2jg. Compatible with Python 3.6+."""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from vcf2jg import __version__
from vcf2jg.converter import convert_file, convert_vc_tcl


def build_parser():
    # type: () -> argparse.ArgumentParser
    p = argparse.ArgumentParser(
        prog="vcf2jg",
        description=(
            "Convert Synopsys VC Formal Tcl scripts to "
            "Cadence JasperGold Tcl scripts."
        ),
    )
    p.add_argument(
        "input",
        type=Path,
        nargs="?",
        help="VC Formal .tcl file (omit to read stdin)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output JasperGold .tcl path (default: <input>_jg.tcl or stdout)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print conversion statistics and warnings to stderr",
    )
    p.add_argument(
        "--stdout",
        action="store_true",
        help="Write result to stdout even when input is a file",
    )
    p.add_argument(
        "--version",
        action="version",
        version="vcf2jg {}".format(__version__),
    )
    return p


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    args = build_parser().parse_args(argv)

    if args.input is None or str(args.input) == "-":
        text = sys.stdin.read()
        result = convert_vc_tcl(text, source_name="<stdin>")
        out_path = args.output
        if out_path and not args.stdout:
            out_path.write_text(result.jasper_tcl, encoding="utf-8")
            if args.verbose:
                print("Wrote {}".format(out_path), file=sys.stderr)
        else:
            sys.stdout.write(result.jasper_tcl)
    else:
        if not args.input.is_file():
            print("error: input not found: {}".format(args.input), file=sys.stderr)
            return 1
        if args.stdout and args.output is None:
            text = args.input.read_text(encoding="utf-8", errors="replace")
            result = convert_vc_tcl(text, source_name=str(args.input))
            sys.stdout.write(result.jasper_tcl)
        else:
            out = args.output
            if out is None and not args.stdout:
                out = args.input.with_name(args.input.stem + "_jg.tcl")
            if args.stdout:
                text = args.input.read_text(encoding="utf-8", errors="replace")
                result = convert_vc_tcl(text, source_name=str(args.input))
                sys.stdout.write(result.jasper_tcl)
            else:
                result = convert_file(args.input, out)
                if args.verbose:
                    print("Wrote {}".format(out), file=sys.stderr)

    if args.verbose:
        print(
            "mapped={}  skipped={}  unmapped={}".format(
                result.mapped_count,
                result.skipped_count,
                result.unmapped_count,
            ),
            file=sys.stderr,
        )
        for w in result.warnings:
            print("warning: {}".format(w), file=sys.stderr)

    # Non-zero if anything unmapped (useful in CI); still writes output
    return 1 if result.unmapped_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
