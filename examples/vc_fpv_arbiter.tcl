# Example VC Formal FPV script (from common public FPV flows)
set_app_var fml_mode_on true
set_fml_appmode FPV

set design arbiter
read_file -top $design -format sverilog -aep all -sva -vcs {-f filelist.f}

create_clock clk -period 100
create_reset rst -high

sim_run -stable
sim_save_reset

check_fv -block
report_fv -list > fpv_results.txt
