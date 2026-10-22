"""
Convert Synopsys VC Formal Tcl scripts to Cadence JasperGold Tcl scripts.

This is a best-effort, line-oriented translator for common FPV flows.
It is not a full Tcl interpreter. Unmapped commands are preserved as
commented warnings so you can finish the conversion by hand.

Compatible with Python 3.6+.
"""

import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Data model (plain classes for Python 3.6 — no dataclasses)
# ---------------------------------------------------------------------------

class ConversionResult(object):
    """Output of a conversion plus diagnostic messages."""

    def __init__(self, jasper_tcl, warnings=None, mapped_count=0,
                 skipped_count=0, unmapped_count=0):
        self.jasper_tcl = jasper_tcl
        self.warnings = warnings if warnings is not None else []
        self.mapped_count = mapped_count
        self.skipped_count = skipped_count
        self.unmapped_count = unmapped_count


class _Ctx(object):
    """Mutable conversion state."""

    def __init__(self):
        self.vars = {}
        self.top = None  # type: Optional[str]
        self.filelist = None  # type: Optional[str]
        self.analyze_done = False
        self.elaborate_done = False
        self.clock_emitted = False
        self.reset_emitted = False
        self.warnings = []  # type: List[str]
        self.mapped = 0
        self.skipped = 0
        self.unmapped = 0
        self.out = []  # type: List[str]
        # Defer elaborate until we have top if analyze came first without top
        self.pending_elaborate_top = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SET_RE = re.compile(
    r"^\s*set\s+(\w+)\s+(.+?)\s*$",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(r"^\s*#")
_EMPTY_RE = re.compile(r"^\s*$")
_VAR_REF_RE = re.compile(r"\$(\w+)|\$\{(\w+)\}")


def _strip_inline_comment(line: str) -> str:
    """Remove Tcl `#` comments that are not inside braces/quotes (simple)."""
    in_brace = 0
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_quote = not in_quote
        elif not in_quote:
            if ch == "{":
                in_brace += 1
            elif ch == "}":
                in_brace = max(0, in_brace - 1)
            elif ch == "#" and in_brace == 0:
                # Tcl treats # as comment only when it is its own word start
                if i == 0 or line[i - 1].isspace():
                    return line[:i].rstrip()
    return line.rstrip()


def _expand_vars(text, variables):
    # type: (str, dict) -> str
    """Expand $var / ${var} using known simple set values."""

    def repl(m):
        name = m.group(1) or m.group(2)
        if name in variables:
            return str(variables[name])
        return m.group(0)

    return _VAR_REF_RE.sub(repl, text)


def _tokenize(line: str) -> List[str]:
    """
    Lightweight Tcl-ish tokenization.

    Uses shlex with posix=True and keeps braces as single tokens when possible.
    Falls back to simple split on failure.
    """
    s = line.strip()
    if not s:
        return []
    try:
        # Tcl often uses {a b c}; shlex with posix handles quotes; braces
        # are not special to shlex, so treat {...} as words manually first.
        parts: List[str] = []
        i = 0
        n = len(s)
        while i < n:
            while i < n and s[i].isspace():
                i += 1
            if i >= n:
                break
            if s[i] == "{":
                depth = 1
                j = i + 1
                while j < n and depth:
                    if s[j] == "{":
                        depth += 1
                    elif s[j] == "}":
                        depth -= 1
                    j += 1
                parts.append(s[i:j])  # includes braces
                i = j
            elif s[i] == '"':
                j = i + 1
                while j < n:
                    if s[j] == "\\" and j + 1 < n:
                        j += 2
                        continue
                    if s[j] == '"':
                        j += 1
                        break
                    j += 1
                parts.append(s[i:j])
                i = j
            else:
                j = i
                while j < n and not s[j].isspace():
                    j += 1
                parts.append(s[i:j])
                i = j
        return parts
    except Exception:
        return s.split()


def _unbrace(tok: str) -> str:
    """Strip surrounding {} or \"\" from a token."""
    if len(tok) >= 2 and tok[0] == "{" and tok[-1] == "}":
        return tok[1:-1]
    if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
        return tok[1:-1]
    return tok


def _get_opt(tokens: Sequence[str], flag: str, default: Optional[str] = None) -> Optional[str]:
    """Return value after -flag if present."""
    for i, t in enumerate(tokens):
        if t == flag and i + 1 < len(tokens):
            return _unbrace(tokens[i + 1])
    return default


def _has_flag(tokens: Sequence[str], flag: str) -> bool:
    return flag in tokens


def _join_continuation(lines: Iterable[str]) -> List[str]:
    """Join Tcl physical lines ending with backslash."""
    logical: List[str] = []
    buf = ""
    for raw in lines:
        line = raw.rstrip("\n")
        if buf:
            # continuation: drop leading whitespace of continued line
            line = line.lstrip()
        if line.endswith("\\") and not line.endswith("\\\\"):
            buf += line[:-1]
            continue
        buf += line
        logical.append(buf)
        buf = ""
    if buf:
        logical.append(buf)
    return logical


def _emit(ctx: _Ctx, line: str = "") -> None:
    ctx.out.append(line)


def _map(ctx: _Ctx, line: str) -> None:
    ctx.mapped += 1
    _emit(ctx, line)


def _skip(ctx: _Ctx, reason: str, original: str) -> None:
    ctx.skipped += 1
    _emit(ctx, f"# [vcf2jg skip] {reason}")
    _emit(ctx, f"# {original}")


def _unmapped(ctx: _Ctx, original: str, hint: str = "") -> None:
    ctx.unmapped += 1
    msg = f"Unmapped command: {original}"
    if hint:
        msg += f" ({hint})"
    ctx.warnings.append(msg)
    _emit(ctx, f"# [vcf2jg TODO] {hint or 'manual review needed'}")
    _emit(ctx, f"# {original}")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _handle_set(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if len(tokens) < 3 or tokens[0].lower() != "set":
        return False
    name = tokens[1]
    value = _unbrace(" ".join(tokens[2:]))
    # Expand nested vars in value
    value = _expand_vars(value, ctx.vars)
    ctx.vars[name] = value

    # Special: set design <top>
    if name in ("design", "top", "TOP", "DESIGN"):
        ctx.top = value
        _emit(ctx, f"# VC Formal: set {name} {value}  →  used as elaborate -top")
        ctx.mapped += 1
        return True

    # Keep simple user variables as Jasper set for later use
    if re.match(r"^[A-Za-z_]\w*$", name):
        _map(ctx, "set {0} {{{1}}}".format(name, value))
        return True

    _skip(ctx, f"set {name} (not needed for JasperGold)", original)
    return True


def _handle_set_app_var(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "set_app_var":
        return False
    _skip(ctx, "VC Formal app var (no JasperGold equivalent)", original)
    return True


def _handle_set_fml_appmode(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "set_fml_appmode":
        return False
    mode = tokens[1].upper() if len(tokens) > 1 else "FPV"
    app_map = {
        "FPV": "Formal Property Verification (default FPV flow)",
        "AEP": "Auto formal / structural checks — review JasperGold Auto Formal or ST apps",
        "COV": "Coverage — review JasperGold coverage / FCA-equivalent flow",
        "FCA": "Formal Coverage Analyzer — review JasperGold coverage flow",
        "SEQ": "Sequential equivalence — review JasperGold SEC app",
        "CCAP": "Connectivity / sequential apps — review JasperGold apps",
    }
    note = app_map.get(mode, f"App mode {mode}")
    _emit(ctx, f"# VC Formal appmode: {mode} → {note}")
    if mode == "FPV":
        ctx.mapped += 1
    else:
        ctx.warnings.append(
            f"App mode {mode} has no 1:1 JasperGold FPV mapping; script uses FPV-style setup."
        )
        ctx.mapped += 1
    return True


def _extract_filelist_from_vcs(vcs_blob: str) -> Optional[str]:
    """Parse -vcs {-f filelist.f ...} style blob for a -f filelist."""
    # common: -f filelist.f  or  -f ./path/file.f
    m = re.search(r"-f\s+(\S+)", vcs_blob)
    if m:
        return m.group(1).strip("'\"")
    return None


def _handle_read_file(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "read_file":
        return False

    top = _get_opt(tokens, "-top")
    fmt = _get_opt(tokens, "-format", "sverilog")
    # -vcs { ... } may be one braced token
    vcs = _get_opt(tokens, "-vcs", "")
    filelist = None
    rtl_files: List[str] = []

    if vcs:
        filelist = _extract_filelist_from_vcs(vcs)
        # also collect bare .sv/.v if present
        for m in re.finditer(r"(\S+\.(?:sv|v|svh|vh))\b", vcs):
            rtl_files.append(m.group(1))

    # Positional file args after options
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("-"):
            # skip option and its arg if present
            if t in (
                "-top",
                "-format",
                "-vcs",
                "-cov",
                "-aep",
                "-sva",
                "-f",
            ):
                if t in ("-sva",):
                    i += 1
                    continue
                i += 2
                continue
            i += 1
            continue
        if t.endswith((".sv", ".v", ".svh", ".vh", ".f")):
            if t.endswith(".f"):
                filelist = _unbrace(t)
            else:
                rtl_files.append(_unbrace(t))
        i += 1

    # -f as explicit flag
    f_opt = _get_opt(tokens, "-f")
    if f_opt:
        filelist = f_opt

    if top:
        top = _expand_vars(top, ctx.vars)
        ctx.top = top
        ctx.vars.setdefault("design", top)

    if filelist:
        filelist = _expand_vars(filelist, ctx.vars)
        ctx.filelist = filelist

    # Language switch for analyze
    lang = "-sv12"
    if fmt and "verilog" in fmt.lower() and "sverilog" not in fmt.lower() and "system" not in fmt.lower():
        lang = "-v2k"

    if not ctx.analyze_done:
        _emit(ctx, "clear -all")
        if filelist:
            _map(ctx, f"analyze {lang} -f {filelist}")
        elif rtl_files:
            files = " ".join(rtl_files)
            _map(ctx, f"analyze {lang} {files}")
        else:
            _unmapped(ctx, original, "read_file without filelist or RTL files")
            return True
        ctx.analyze_done = True
    else:
        if filelist:
            _map(ctx, f"analyze {lang} -f {filelist}")
        elif rtl_files:
            _map(ctx, f"analyze {lang} {' '.join(rtl_files)}")

    if _has_flag(tokens, "-sva") or "sva" in original.lower():
        _emit(ctx, "# SVA enabled via analyze (properties in design / bind files)")

    top_name = ctx.top
    if top_name and not ctx.elaborate_done:
        _map(ctx, f"elaborate -top {top_name}")
        ctx.elaborate_done = True
    elif not top_name:
        ctx.pending_elaborate_top = True
        _emit(ctx, "# [vcf2jg] elaborate -top <TOP>  ;# set after design/top is known")

    return True


def _handle_create_clock(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "create_clock":
        return False
    # VC: create_clock clk -period 100
    # or: create_clock -name clk -period 100 [get_ports clk]
    period = _get_opt(tokens, "-period")
    name = _get_opt(tokens, "-name")
    # positional first non-option as clock signal
    clk = name
    for t in tokens[1:]:
        if t.startswith("-"):
            continue
        if t.startswith("["):
            continue
        clk = _expand_vars(_unbrace(t), ctx.vars)
        break
    if not clk:
        _unmapped(ctx, original, "create_clock without clock name")
        return True
    note = f"  ;# period {period} (JG ignores absolute period for FPV)" if period else ""
    _map(ctx, f"clock {clk}{note}")
    ctx.clock_emitted = True
    return True


def _handle_create_reset(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "create_reset":
        return False
    # VC: create_reset rst -high | -low
    # or: create_reset -name rst -high
    active_high = _has_flag(tokens, "-high")
    active_low = _has_flag(tokens, "-low")
    name = _get_opt(tokens, "-name")
    rst = name
    for t in tokens[1:]:
        if t.startswith("-"):
            continue
        rst = _expand_vars(_unbrace(t), ctx.vars)
        break
    if not rst:
        _unmapped(ctx, original, "create_reset without reset name")
        return True
    if active_low or (not active_high and re.search(r"(n|N|_n|_N|b)$", rst)):
        # active-low: Jasper uses inverted expression
        if active_low:
            _map(ctx, f"reset ~{rst}  ;# active-low reset")
        else:
            # default VC without polarity often active-high; if name looks n-suffixed assume low
            _map(ctx, f"reset ~{rst}  ;# inferred active-low from name; verify polarity")
            ctx.warnings.append(
                f"Reset '{rst}' polarity inferred as active-low from name; confirm."
            )
    else:
        # active-high (explicit -high or default)
        _map(ctx, f"reset {rst}  ;# active-high reset")
    ctx.reset_emitted = True
    return True


def _handle_sim_run(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "sim_run":
        return False
    _skip(
        ctx,
        "sim_run -stable: JasperGold applies reset sequence via 'reset'",
        original,
    )
    return True


def _handle_sim_save_reset(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "sim_save_reset":
        return False
    _skip(
        ctx,
        "sim_save_reset: covered by JasperGold reset initialization",
        original,
    )
    return True


def _handle_check_fv(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "check_fv":
        return False
    # check_fv -block | -bg | properties...
    if _has_flag(tokens, "-block") or _has_flag(tokens, "-bg"):
        _map(ctx, "prove -all")
    else:
        # optional property list
        props = [t for t in tokens[1:] if not t.startswith("-")]
        if props:
            prop_list = " ".join(_expand_vars(_unbrace(p), ctx.vars) for p in props)
            _map(ctx, f"prove -property {{{prop_list}}}")
        else:
            _map(ctx, "prove -all")
    return True


def _handle_report_fv(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "report_fv":
        return False
    # report_fv -list > file
    redirect = None
    if ">" in tokens:
        idx = tokens.index(">")
        if idx + 1 < len(tokens):
            redirect = tokens[idx + 1]
    # also handle `report_fv -list > file` parsed as separate
    m = re.search(r">\s*(\S+)", original)
    if m:
        redirect = m.group(1)

    if redirect:
        _map(ctx, f"report_proofs -verbose > {redirect}")
    else:
        _map(ctx, "report_proofs -verbose")
    return True


def _handle_fvassume(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] not in ("fvassume", "assume"):
        return False
    # VC: fvassume -clock clk -expr {foo} -depth 1
    expr = _get_opt(tokens, "-expr")
    if not expr:
        # maybe: assume {expr}  (already Jasper-like)
        if len(tokens) >= 2:
            body = " ".join(tokens[1:])
            _map(ctx, f"assume {_unbrace(body) if body.startswith('{') else body}")
            return True
        _unmapped(ctx, original, "fvassume without -expr")
        return True
    expr = _expand_vars(expr, ctx.vars)
    _map(ctx, f"assume {{{expr}}}")
    depth = _get_opt(tokens, "-depth")
    if depth:
        _emit(ctx, f"# note: VC -depth {depth} on assume has no direct 1:1; review if needed")
    return True


def _handle_fvassert(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] not in ("fvassert", "assert"):
        return False
    expr = _get_opt(tokens, "-expr")
    if expr:
        expr = _expand_vars(expr, ctx.vars)
        _map(ctx, f"assert -name auto_assert {{{expr}}}")
        return True
    if len(tokens) >= 2:
        body = " ".join(tokens[1:])
        _map(ctx, f"assert {_unbrace(body) if body.startswith('{') else body}")
        return True
    _unmapped(ctx, original)
    return True


def _handle_current_design(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "current_design":
        return False
    if len(tokens) > 1:
        top = _expand_vars(_unbrace(tokens[1]), ctx.vars)
        ctx.top = top
        if ctx.analyze_done and not ctx.elaborate_done:
            _map(ctx, f"elaborate -top {top}")
            ctx.elaborate_done = True
        else:
            _emit(ctx, f"# current_design {top}")
            ctx.mapped += 1
    return True


def _handle_source(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] != "source":
        return False
    path = _expand_vars(_unbrace(tokens[1]), ctx.vars) if len(tokens) > 1 else "?"
    _emit(ctx, f"# VC Formal sourced: {path}")
    _emit(ctx, f"# Consider: source {{{path}}}  if the sourced file is also converted")
    ctx.warnings.append(f"'source {path}' not auto-inlined; convert that file separately.")
    ctx.unmapped += 1
    return True


def _handle_quit_exit(ctx: _Ctx, tokens: List[str], original: str) -> bool:
    if tokens[0] in ("quit", "exit"):
        _map(ctx, "exit")
        return True
    return False


# Order matters: more specific first
_HANDLERS = [
    _handle_set_app_var,
    _handle_set_fml_appmode,
    _handle_read_file,
    _handle_create_clock,
    _handle_create_reset,
    _handle_sim_run,
    _handle_sim_save_reset,
    _handle_check_fv,
    _handle_report_fv,
    _handle_fvassume,
    _handle_fvassert,
    _handle_current_design,
    _handle_source,
    _handle_quit_exit,
    _handle_set,  # generic set last among set*
]


def _dispatch(ctx: _Ctx, original: str) -> None:
    line = _strip_inline_comment(original)
    if _EMPTY_RE.match(line or ""):
        _emit(ctx, "")
        return
    if _COMMENT_RE.match(original):
        # keep user comments
        _emit(ctx, original.rstrip())
        return

    expanded = _expand_vars(line, ctx.vars)
    tokens = _tokenize(expanded)
    if not tokens:
        _emit(ctx, "")
        return

    cmd = tokens[0]

    for handler in _HANDLERS:
        if handler(ctx, tokens, expanded):
            return

    # Known no-ops / common leftovers
    if cmd in (
        "set_fml_var",
        "set_message_info",
        "suppress_message",
        "unsuppress_message",
        "set_host_options",
        "start_gui",
        "gui_start",
    ):
        _skip(ctx, f"{cmd} not used in JasperGold batch FPV", expanded)
        return

    _unmapped(ctx, expanded, f"no mapping for '{cmd}'")


def convert_vc_tcl(text: str, source_name: str = "<input>") -> ConversionResult:
    """
    Convert VC Formal Tcl source text to JasperGold Tcl.

    Parameters
    ----------
    text:
        Full contents of a VC Formal .tcl file.
    source_name:
        Name used in the generated header comment.
    """
    ctx = _Ctx()
    logical_lines = _join_continuation(text.splitlines())

    header = [
        "# =============================================================================",
        f"# Auto-generated by vcf2jg from: {source_name}",
        "# Review [vcf2jg TODO] and [vcf2jg skip] comments before running in JasperGold.",
        "# =============================================================================",
        "",
    ]
    for h in header:
        _emit(ctx, h)

    for line in logical_lines:
        _dispatch(ctx, line)

    # If elaborate was deferred
    if ctx.pending_elaborate_top and ctx.top and not ctx.elaborate_done:
        # insert elaborate after last analyze — simpler: append with note
        _emit(ctx, "")
        _emit(ctx, f"elaborate -top {ctx.top}  ;# deferred (top resolved late)")
        ctx.elaborate_done = True
        ctx.mapped += 1

    # Ensure minimal skeleton if user only had set_fml bits
    if not ctx.analyze_done:
        ctx.warnings.append(
            "No read_file/analyze found. JasperGold script may be incomplete."
        )

    body = "\n".join(ctx.out)
    if not body.endswith("\n"):
        body += "\n"

    return ConversionResult(
        jasper_tcl=body,
        warnings=ctx.warnings,
        mapped_count=ctx.mapped,
        skipped_count=ctx.skipped,
        unmapped_count=ctx.unmapped,
    )


def convert_file(
    input_path: Path,
    output_path: Optional[Path] = None,
) -> ConversionResult:
    """Convert a VC Formal Tcl file; optionally write JasperGold output."""
    text = input_path.read_text(encoding="utf-8", errors="replace")
    result = convert_vc_tcl(text, source_name=str(input_path))
    if output_path is None:
        output_path = input_path.with_name(input_path.stem + "_jg.tcl")
    output_path.write_text(result.jasper_tcl, encoding="utf-8")
    return result
