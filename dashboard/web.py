import os

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse


API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")

app = FastAPI(title="Store Intelligence Dashboard", version="1.0.0")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Store Intelligence Dashboard</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f7f8fa;
      color: #17202a;
    }
    * { box-sizing: border-box; }
    body { margin: 0; }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding: 20px 28px;
      border-bottom: 1px solid #d9dee7;
      background: #ffffff;
    }
    h1 { margin: 0; font-size: 22px; font-weight: 700; }
    main { max-width: 1180px; margin: 0 auto; padding: 24px; }
    .status { color: #506070; font-size: 14px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-bottom: 22px;
    }
    .metric, .table-wrap {
      background: #ffffff;
      border: 1px solid #d9dee7;
      border-radius: 8px;
      padding: 16px;
    }
    .label { color: #667085; font-size: 13px; margin-bottom: 8px; }
    .value { font-size: 28px; font-weight: 750; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { padding: 12px 10px; text-align: left; border-bottom: 1px solid #edf0f5; }
    th { color: #667085; font-weight: 650; }
    tr:last-child td { border-bottom: 0; }
    .empty { padding: 28px; color: #667085; text-align: center; }
    @media (max-width: 640px) {
      header { align-items: flex-start; flex-direction: column; padding: 18px; }
      main { padding: 16px; }
      .table-wrap { overflow-x: auto; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Store Intelligence Dashboard</h1>
    <div class="status" id="status">Loading...</div>
  </header>
  <main>
    <section class="grid">
      <div class="metric"><div class="label">Stores</div><div class="value" id="stores">0</div></div>
      <div class="metric"><div class="label">Visitors</div><div class="value" id="visitors">0</div></div>
      <div class="metric"><div class="label">Avg Conversion</div><div class="value" id="conversion">0.0%</div></div>
      <div class="metric"><div class="label">Queue Depth</div><div class="value" id="queue">0</div></div>
    </section>
    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Store</th>
            <th>Events</th>
            <th>Visitors</th>
            <th>Conversion</th>
            <th>Queue</th>
            <th>Abandonment</th>
            <th>Last Event</th>
          </tr>
        </thead>
        <tbody id="rows">
          <tr><td colspan="7" class="empty">Waiting for data...</td></tr>
        </tbody>
      </table>
    </section>
  </main>
  <script>
    const pct = value => `${((value || 0) * 100).toFixed(1)}%`;
    const time = value => value ? new Date(value).toLocaleString() : "-";

    async function refresh() {
      const status = document.getElementById("status");
      try {
        const response = await fetch("/api/summary");
        const data = await response.json();
        const stores = data.stores || [];
        const totalVisitors = stores.reduce((sum, s) => sum + (s.metrics.unique_visitors || 0), 0);
        const totalQueue = stores.reduce((sum, s) => sum + (s.metrics.current_queue_depth || 0), 0);
        const avgConversion = stores.length
          ? stores.reduce((sum, s) => sum + (s.metrics.conversion_rate || 0), 0) / stores.length
          : 0;

        document.getElementById("stores").textContent = stores.length;
        document.getElementById("visitors").textContent = totalVisitors;
        document.getElementById("conversion").textContent = pct(avgConversion);
        document.getElementById("queue").textContent = totalQueue;

        const rows = document.getElementById("rows");
        if (!stores.length) {
          rows.innerHTML = '<tr><td colspan="7" class="empty">No events ingested yet.</td></tr>';
        } else {
          rows.innerHTML = stores.map(store => `
            <tr>
              <td>${store.store_id}</td>
              <td>${store.event_count}</td>
              <td>${store.metrics.unique_visitors ?? 0}</td>
              <td>${pct(store.metrics.conversion_rate)}</td>
              <td>${store.metrics.current_queue_depth ?? 0}</td>
              <td>${pct(store.metrics.abandonment_rate)}</td>
              <td>${time(store.last_event_at)}</td>
            </tr>
          `).join("");
        }
        status.textContent = `Updated ${new Date().toLocaleTimeString()}`;
      } catch (error) {
        status.textContent = "Dashboard cannot reach API";
      }
    }

    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


@app.get("/api/summary")
async def summary():
    async with httpx.AsyncClient(timeout=5.0) as client:
        stores_response = await client.get(f"{API_URL}/stores")
        stores_response.raise_for_status()
        stores = stores_response.json().get("stores", [])

        results = []
        for store in stores:
            store_id = store["store_id"]
            metrics_response = await client.get(f"{API_URL}/stores/{store_id}/metrics")
            metrics = metrics_response.json() if metrics_response.status_code == 200 else {}
            results.append({**store, "metrics": metrics})

    return {"stores": results}
