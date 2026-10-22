# vcf2jg — VC Formal Tcl → JasperGold Tcl

Python converter that translates common **Synopsys VC Formal** run scripts into **Cadence JasperGold** Tcl.

Requires **Python 3.6+** (works on older RHEL/CentOS servers).

This is a **best-effort FPV flow translator**, not a full Tcl interpreter. Tool-specific apps (AEP, coverage, SEC) and advanced options need human review (see `[vcf2jg TODO]` comments in the output).

## Install

```bash
cd vcf2jg
pip install -e .
# or run without install:
PYTHONPATH=. python3 -m vcf2jg examples/vc_fpv_arbiter.tcl -o /tmp/out.tcl -v
```

## Usage

```bash
# Write <input>_jg.tcl next to the input
vcf2jg path/to/vc_script.tcl

# Explicit output path
vcf2jg path/to/vc_script.tcl -o jg_run.tcl

# Stdout
vcf2jg path/to/vc_script.tcl --stdout

# From stdin
vcf2jg - < vc_script.tcl -o jg_run.tcl

# Verbose: stats + warnings on stderr
vcf2jg path/to/vc_script.tcl -v
```

## What gets converted

| VC Formal | JasperGold |
|-----------|------------|
| `set_fml_appmode FPV` | Comment (FPV-style flow) |
| `set_app_var fml_mode_on true` | Skipped |
| `read_file -top X -format sverilog -sva -vcs {-f flist.f}` | `clear -all` / `analyze -sv12 -f flist.f` / `elaborate -top X` |
| `create_clock clk -period N` | `clock clk` |
| `create_reset rst -high` | `reset rst` |
| `create_reset rst_n -low` | `reset ~rst_n` |
| `sim_run -stable` / `sim_save_reset` | Skipped (covered by JG `reset`) |
| `check_fv -block` | `prove -all` |
| `report_fv -list > file` | `report_proofs -verbose > file` |
| `fvassume -expr {...}` | `assume {...}` |
| `set design foo` | Captured for `-top` / optional `set` |

SVA modules, bind files, and RTL are **not** rewritten—only the tool control script.

## Example

**Input** (`examples/vc_fpv_arbiter.tcl`):

```tcl
set_fml_appmode FPV
set design arbiter
read_file -top $design -format sverilog -sva -vcs {-f filelist.f}
create_clock clk -period 100
create_reset rst -high
sim_run -stable
sim_save_reset
check_fv -block
report_fv -list > fpv_results.txt
```

**Output** (simplified):

```tcl
clear -all
analyze -sv12 -f filelist.f
elaborate -top arbiter
clock clk
reset rst  ;# active-high reset
prove -all
report_proofs -verbose > fpv_results.txt
```

## Limitations

- Not a complete Tcl parser (`if`, `proc`, complex `expr`, nested `source` inlining).
- AEP / COV / SEQ / connectivity apps are only annotated—FPV-style skeleton is emitted.
- Clock period, grid, licenses, and engine knobs differ by tool and are not fully mapped.
- Always review output before production sign-off runs.

## Tests

```bash
cd vcf2jg
PYTHONPATH=. python3 -m unittest tests.test_converter -v
```

## Project layout

```
vcf2jg/
  vcf2jg/
    converter.py   # mapping engine
    cli.py         # CLI
  examples/        # sample VC Formal scripts
  tests/
```
