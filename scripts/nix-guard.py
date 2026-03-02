#!/usr/bin/env python3
import subprocess
import time
import os
import signal

# --- CONFIGURATION ---
CPU_THRESHOLD = 80.0  # Percentage
CHECK_INTERVAL = 10    # Seconds between checks
STRIKE_LIMIT = 3       # Number of consecutive strikes before killing
PROCESS_NAMES = ["cc1plus", "g++", "gcc", "clang"]
# ---------------------

strikes = {}

def get_high_cpu_processes():
    """Returns a list of (pid, cpu_percent, comm) for targeted processes."""
    try:
        # Get PID, %CPU, and command name for all processes
        output = subprocess.check_output(["ps", "-eo", "pid,pcpu,comm", "--no-headers"], text=True)
        lines = output.strip().split('
')
        
        high_cpu = []
        for line in lines:
            parts = line.split()
            if len(parts) < 3:
                continue
            
            pid = int(parts[0])
            cpu = float(parts[1])
            comm = parts[2]
            
            if cpu > CPU_THRESHOLD and any(name in comm for name in PROCESS_NAMES):
                high_cpu.append((pid, cpu, comm))
        return high_cpu
    except Exception as e:
        print(f"Error checking processes: {e}")
        return []

def kill_build_tree(pid):
    """Attempt to find the root 'nix build' or 'nixos-rebuild' and kill it."""
    try:
        # Find the parent of this process recursively until we hit a nix-related command or root
        current_pid = pid
        while True:
            parent_output = subprocess.check_output(["ps", "-o", "ppid,comm", "-p", str(current_pid), "--no-headers"], text=True)
            ppid, pcomm = parent_output.strip().split()
            ppid = int(ppid)
            
            # If the parent is a nix command, that's our target
            if "nix" in pcomm.lower() or "nixos-rebuild" in pcomm.lower():
                print(f"Targeting build root: PID {ppid} ({pcomm})")
                os.kill(ppid, signal.SIGTERM)
                time.sleep(2)
                os.kill(ppid, signal.SIGKILL) # Ensure it's gone
                return True
            
            if ppid <= 1:
                break
            current_pid = ppid
            
        # If no nix parent found, just kill the process itself
        print(f"Killing individual process: PID {pid}")
        os.kill(pid, signal.SIGKILL)
        return True
    except Exception as e:
        print(f"Failed to kill PID {pid}: {e}")
        return False

print(f"Nix-Guard started. Threshold: {CPU_THRESHOLD}%, Interval: {CHECK_INTERVAL}s")

try:
    while True:
        current_high = get_high_cpu_processes()
        current_pids = {p[0] for p in current_high}
        
        # Update strikes
        for pid, cpu, comm in current_high:
            strikes[pid] = strikes.get(pid, 0) + 1
            print(f"[!] PID {pid} ({comm}) is at {cpu}% CPU. Strike {strikes[pid]}/{STRIKE_LIMIT}")
            
            if strikes[pid] >= STRIKE_LIMIT:
                print(f"!!! CPU limit exceeded. Terminating build tree for PID {pid}...")
                kill_build_tree(pid)
                # Cleanup strikes for this PID
                if pid in strikes: del strikes[pid]
        
        # Cleanup strikes for PIDs that are no longer high-CPU or gone
        pids_to_remove = [pid for pid in strikes if pid not in current_pids]
        for pid in pids_to_remove:
            del strikes[pid]
            
        time.sleep(CHECK_INTERVAL)
except KeyboardInterrupt:
    print("
Nix-Guard stopped.")
