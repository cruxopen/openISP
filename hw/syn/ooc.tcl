# Out-of-context synthesis + implementation of an RTL top on the K26 SOM part.
# usage: vivado -mode batch -source hw/syn/ooc.tcl -tclargs <outdir> <top> <period_ns> <files...>
set out [lindex $argv 0]; set top [lindex $argv 1]; set period [lindex $argv 2]
set files [lrange $argv 3 end]
file mkdir $out
set_param general.maxThreads 4
read_verilog -sv $files
synth_design -top $top -part xck26-sfvc784-2LV-c -mode out_of_context -flatten_hierarchy rebuilt
# clock port: clk (blocks, isp_top) or aclk (isp_axil)
set cp [get_ports -quiet clk]
if {[llength $cp] == 0} { set cp [get_ports aclk] }
create_clock -period $period -name clk $cp
report_utilization -file $out/util_synth.rpt
opt_design; place_design; phys_opt_design; route_design
set wns [get_property SLACK [get_timing_paths -max_paths 1 -setup]]
puts "OOC_RESULT top=$top period=$period WNS=$wns"
report_utilization -file $out/util.rpt
report_utilization -hierarchical -hierarchical_depth 2 -file $out/util_hier.rpt
report_timing_summary -max_paths 10 -file $out/timing.rpt
write_checkpoint -force $out/post_route.dcp
# failing setup endpoints per block (2 levels of hierarchy)
set agg [dict create]
foreach p [get_timing_paths -setup -max_paths 100000 -nworst 1 -slack_lesser_than 0] {
  set ep [get_property NAME [get_property ENDPOINT_PIN $p]]
  set parts [split $ep /]
  set key [join [lrange $parts 0 [expr {min(1, [llength $parts] - 2)}]] /]
  set s [get_property SLACK $p]
  if {![dict exists $agg $key]} { dict set agg $key [list 0 $s] }
  lassign [dict get $agg $key] n w
  dict set agg $key [list [incr n] [expr {min($w, $s)}]]
}
set fh [open $out/fail_by_block.txt w]
dict for {k v} $agg { puts $fh "$k [lindex $v 0] [lindex $v 1]" }
close $fh
