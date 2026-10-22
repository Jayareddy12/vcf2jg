set_app_var fml_mode_on true
set_fml_appmode FPV

set design ntt_butterfly
read_file -top $design -format sverilog -sva -vcs {-f ../filelist.f}

create_clock clk -period 10
create_reset rst_n -low

sim_run -stable
sim_save_reset

fvassume -clock clk -expr {scan_en == 1'b0}
fvassume -clock clk -expr {!test_mode}

check_fv -block
report_fv -list > results.txt
