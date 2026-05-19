# LAN CPU Load Balancer

Web-based load balancer for PCs on the same LAN. A coordinator node distributes heavy CPU tasks to worker PCs so all machines are utilized evenly.

## Features
- Web UI to add/remove worker IP addresses (e.g., `192.168.1.20:8001`)
- Broadcast a CPU-heavy task to all workers in parallel
- Adjustable duration and intensity (set to `90+` for high CPU)
- Worker mode runs one process per core to push utilization toward ~90%+

## Run

### 1) Start worker on each PC
```bash
python3 app.py worker --port 8001
```

### 2) Start coordinator on one PC
```bash
python3 app.py coordinator --port 8000
```

Open `http://<coordinator-ip>:8000` in browser.

## Notes
- Ensure firewall allows the ports.
- All PCs must be in same LAN/subnet.
- CPU usage depends on OS scheduler/background load; use intensity `95` to target near full utilization.
