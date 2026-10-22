"""Unit tests for vcf2jg converter."""

from pathlib import Path
import unittest

from vcf2jg.converter import convert_vc_tcl

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class TestConverter(unittest.TestCase):
    def test_fpv_arbiter_basic(self):
        text = (EXAMPLES / "vc_fpv_arbiter.tcl").read_text()
        result = convert_vc_tcl(text, source_name="vc_fpv_arbiter.tcl")
        out = result.jasper_tcl

        self.assertIn("clear -all", out)
        self.assertIn("analyze -sv12 -f filelist.f", out)
        self.assertIn("elaborate -top arbiter", out)
        self.assertIn("clock clk", out)
        self.assertIn("reset rst", out)
        self.assertIn("prove -all", out)
        self.assertIn("report_proofs", out)
        self.assertTrue(
            any(
                "sim_run" in line and line.strip().startswith("#")
                for line in out.splitlines()
            )
        )
        self.assertGreaterEqual(result.mapped_count, 5)

    def test_active_low_reset_and_assume(self):
        text = (EXAMPLES / "vc_fpv_with_assume.tcl").read_text()
        result = convert_vc_tcl(text)
        out = result.jasper_tcl

        self.assertIn("elaborate -top ntt_butterfly", out)
        self.assertIn("reset ~rst_n", out)
        self.assertIn("assume {scan_en == 1'b0}", out)
        self.assertIn("assume {!test_mode}", out)
        self.assertIn("prove -all", out)

    def test_variable_expansion(self):
        text = """
set design my_top
set flist ./files.f
read_file -top $design -format sverilog -sva -vcs {-f $flist}
create_clock clk -period 5
create_reset reset -high
check_fv -block
"""
        result = convert_vc_tcl(text)
        self.assertIn("elaborate -top my_top", result.jasper_tcl)
        self.assertIn("analyze -sv12 -f ./files.f", result.jasper_tcl)

    def test_line_continuation(self):
        text = r"""
set design \
  foo
read_file -top $design -format sverilog -sva -vcs {-f a.f}
"""
        result = convert_vc_tcl(text)
        self.assertIn("elaborate -top foo", result.jasper_tcl)

    def test_unmapped_command_warning(self):
        text = "some_unknown_vc_cmd -foo bar\n"
        result = convert_vc_tcl(text)
        self.assertGreaterEqual(result.unmapped_count, 1)
        self.assertIn("TODO", result.jasper_tcl)
        self.assertTrue(result.warnings)

    def test_create_reset_polarity(self):
        high = convert_vc_tcl("create_reset rst -high\n").jasper_tcl
        low = convert_vc_tcl("create_reset rst_n -low\n").jasper_tcl
        self.assertIn("reset rst", high)
        self.assertIn("reset ~rst_n", low)

    def test_stdin_style_empty_has_header(self):
        result = convert_vc_tcl("")
        self.assertIn("vcf2jg", result.jasper_tcl)


if __name__ == "__main__":
    unittest.main()
