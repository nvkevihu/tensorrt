#!/bin/bash

LOGFILE=$1

gpu_monitor()  {
  while [ 1 -eq 1 ]; do
    nvidia-smi --query-gpu=pstate,clocks.gr,clocks.mem,power.draw,pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max,display_mode,display_active,clocks_throttle_reasons.gpu_idle,clocks_throttle_reasons.applications_clocks_setting,clocks_throttle_reasons.sw_power_cap,clocks_throttle_reasons.hw_slowdown,clocks_throttle_reasons.hw_thermal_slowdown,clocks_throttle_reasons.hw_power_brake_slowdown,clocks_throttle_reasons.sync_boost,memory.used,utilization.gpu,utilization.memory,ecc.mode.current,enforced.power.limit,temperature.gpu --format=csv >> ${LOGFILE}
  done
}

gpu_monitor &
echo $!
