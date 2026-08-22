# SPDX-License-Identifier: GPL-3.0-only
#
# Timing constraints for the counter demonstration project.

create_clock -name clk -period 20.000 [get_ports {clk}]   ;# 50 MHz

derive_pll_clocks
derive_clock_uncertainty

# Buttons, reset and the LED outputs are asynchronous to the system clock:
# nothing outside the device samples them against it.
set_false_path -from [get_ports {rst_n}] -to [all_registers]
set_false_path -from [get_ports {btn_n[*]}] -to [all_registers]
set_false_path -from * -to [get_ports {led[*]}]
set_false_path -from * -to [get_ports {seg_n[*]}]
set_false_path -from * -to [get_ports {heartbeat}]
