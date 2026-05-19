#!/usr/bin/env python3
import argparse
import json
import math
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing import Process, cpu_count
from urllib import request

COORDINATOR_HTML = """<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <title>LAN Load Balancer</title>
  <style>
    body{font-family:Arial,sans-serif;margin:24px;max-width:900px}
    input,button{padding:8px;margin:4px}
    table{border-collapse:collapse;width:100%;margin-top:16px}
    th,td{border:1px solid #ddd;padding:8px;text-align:left}
    .ok{color:green}.err{color:#b00}
  </style>
</head>
<body>
<h1>LAN CPU Load Balancer</h1>
<p>Add worker PC IP addresses (same LAN), then dispatch CPU-heavy jobs across all workers evenly.</p>
<div>
  <input id='workerIp' placeholder='192.168.1.25:8001'>
  <button onclick='addWorker()'>Add Worker</button>
  <button onclick='removeWorker()'>Remove Selected</button>
</div>
<div>
  <label>Duration (sec): <input id='duration' value='20' type='number' min='1'></label>
  <label>Intensity (% CPU target): <input id='intensity' value='95' type='number' min='1' max='100'></label>
  <button onclick='startLoad()'>Start Distributed Load</button>
</div>
<table>
  <thead><tr><th></th><th>Worker</th><th>Status</th><th>Last Result</th></tr></thead>
  <tbody id='workers'></tbody>
</table>
<pre id='log'></pre>
<script>
async function api(path, method='GET', body=null){
  const opts={method,headers:{'Content-Type':'application/json'}};
  if(body) opts.body=JSON.stringify(body);
  const r=await fetch(path,opts); return r.json();
}
function log(msg){document.getElementById('log').textContent += msg+'\n';}
async function refresh(){
  const data = await api('/api/workers');
  const tbody = document.getElementById('workers');
  tbody.innerHTML='';
  for(const w of data.workers){
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input type='radio' name='sel' value='${w.address}'></td><td>${w.address}</td><td class='${w.status==='reachable'?'ok':'err'}'>${w.status}</td><td>${w.last_result||''}</td>`;
    tbody.appendChild(tr);
  }
}
async function addWorker(){
  const address=document.getElementById('workerIp').value.trim();
  if(!address) return;
  await api('/api/workers','POST',{address});
  document.getElementById('workerIp').value='';
  await refresh();
}
async function removeWorker(){
  const sel=document.querySelector("input[name='sel']:checked");
  if(!sel) return;
  await api('/api/workers','DELETE',{address:sel.value});
  await refresh();
}
async function startLoad(){
  const duration=parseInt(document.getElementById('duration').value,10);
  const intensity=parseInt(document.getElementById('intensity').value,10);
  log(`Dispatching ${duration}s task to all workers at ${intensity}% intensity...`);
  const result=await api('/api/dispatch','POST',{duration,intensity});
  log(JSON.stringify(result,null,2));
  await refresh();
}
refresh(); setInterval(refresh,5000);
</script>
</body>
</html>"""


def cpu_burn(duration_sec: int, intensity: int) -> dict:
    workers = max(1, cpu_count())
    duty = max(1, min(100, intensity)) / 100.0
    end = time.time() + duration_sec

    def burner():
      while time.time() < end:
        burst_end = time.time() + duty * 0.1
        x = random.random() + 1.0
        while time.time() < burst_end:
            x = math.sqrt(x * x + 123.456)
        sleep_for = (1 - duty) * 0.1
        if sleep_for > 0:
            time.sleep(sleep_for)

    procs = [Process(target=burner) for _ in range(workers)]
    t0 = time.time()
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    elapsed = round(time.time() - t0, 2)
    return {
        "message": "CPU burn complete",
        "duration": duration_sec,
        "elapsed": elapsed,
        "local_cores_used": workers,
        "target_intensity": intensity,
        "note": "Uses busy workers per CPU core; in practice this typically drives ~90%+ CPU when intensity >= 90."
    }


class State:
    workers = {}
    lock = threading.Lock()


class CoordinatorHandler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        ln = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(ln).decode() or "{}")

    def do_GET(self):
        if self.path == "/":
            body = COORDINATOR_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/workers":
            with State.lock:
                workers = [{"address": a, **meta} for a, meta in State.workers.items()]
            return self._json(200, {"workers": workers})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/workers":
            data = self._read_json()
            address = data.get("address", "").strip()
            if not address:
                return self._json(400, {"error": "address required"})
            with State.lock:
                State.workers.setdefault(address, {"status": "unknown", "last_result": ""})
            return self._json(200, {"ok": True})

        if self.path == "/api/dispatch":
            data = self._read_json()
            duration = int(data.get("duration", 20))
            intensity = int(data.get("intensity", 95))
            with State.lock:
                addresses = list(State.workers.keys())

            def run_remote(address):
                url = f"http://{address}/run-task"
                payload = json.dumps({"duration": duration, "intensity": intensity}).encode()
                req = request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with request.urlopen(req, timeout=duration + 15) as resp:
                        result = json.loads(resp.read().decode())
                    status = "reachable"
                except Exception as e:
                    result = {"error": str(e)}
                    status = "unreachable"
                with State.lock:
                    if address in State.workers:
                        State.workers[address]["status"] = status
                        State.workers[address]["last_result"] = json.dumps(result)
                return {"worker": address, "status": status, "result": result}

            results = []
            with ThreadPoolExecutor(max_workers=max(1, len(addresses))) as ex:
                futs = [ex.submit(run_remote, a) for a in addresses]
                for f in as_completed(futs):
                    results.append(f.result())
            return self._json(200, {"dispatched": len(addresses), "results": results})

        self._json(404, {"error": "not found"})

    def do_DELETE(self):
        if self.path == "/api/workers":
            data = self._read_json()
            address = data.get("address", "").strip()
            with State.lock:
                State.workers.pop(address, None)
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})


class WorkerHandler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        ln = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(ln).decode() or "{}")

    def do_POST(self):
        if self.path != "/run-task":
            return self._json(404, {"error": "not found"})
        data = self._read_json()
        duration = int(data.get("duration", 20))
        intensity = int(data.get("intensity", 95))
        result = cpu_burn(duration, intensity)
        self._json(200, result)


def run_server(mode: str, host: str, port: int):
    handler = CoordinatorHandler if mode == "coordinator" else WorkerHandler
    server = ThreadingHTTPServer((host, port), handler)
    print(f"{mode} listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LAN CPU Load Balancer")
    parser.add_argument("mode", choices=["coordinator", "worker"])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_server(args.mode, args.host, args.port)
