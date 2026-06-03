"""
Live terminal dashboard for Store Intelligence Pipeline.
Subscribes to Redis pub/sub channels and displays real-time
store metrics using the Rich library.

Run: python dashboard/live.py
Or:  docker compose up dashboard
"""

import os
import json
import time
import threading
import httpx
from datetime import datetime, timezone
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL", "http://localhost:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REFRESH_RATE = 2

STORE_IDS = [
    "ST1008",
    "ST2008",
]


class DashboardState:
    """Thread-safe store for live metric values."""

    def __init__(self):
        self.lock = threading.Lock()
        self.metrics = {}
        self.anomalies = {}
        self.last_event = {}
        self.event_counts = {}
        self.connected = True
        self.last_update = datetime.now(timezone.utc)

    def update_from_event(self, event: dict):
        with self.lock:
            store_id = event.get("store_id")
            if store_id:
                self.last_event[store_id] = event.get(
                    "timestamp",
                    datetime.now(timezone.utc).isoformat()
                )
                self.event_counts[store_id] = (
                    self.event_counts.get(store_id, 0) + 1
                )
                self.last_update = datetime.now(timezone.utc)

    def update_metrics(self, store_id: str, metrics: dict):
        with self.lock:
            self.metrics[store_id] = metrics
            self.last_update = datetime.now(timezone.utc)

    def update_anomalies(self, store_id: str, anomalies: list):
        with self.lock:
            self.anomalies[store_id] = anomalies
            self.last_update = datetime.now(timezone.utc)

    def set_connected(self, status: bool):
        with self.lock:
            self.connected = status


def fetch_metrics_loop(state: DashboardState):
    """
    Background thread. Every 5 seconds fetches fresh metrics
    and anomalies from the API for all STORE_IDS.
    """
    while True:
        try:
            client = httpx.Client(timeout=3.0)
            for store_id in STORE_IDS:
                try:
                    resp = client.get(
                        f"{API_URL}/stores/{store_id}/metrics"
                    )
                    if resp.status_code == 200:
                        state.update_metrics(store_id, resp.json())
                except Exception:
                    pass

                try:
                    resp = client.get(
                        f"{API_URL}/stores/{store_id}/anomalies"
                    )
                    if resp.status_code == 200:
                        state.update_anomalies(store_id, resp.json())
                except Exception:
                    pass
            client.close()
        except Exception:
            pass
        time.sleep(5)


def redis_subscriber(state: DashboardState):
    """
    Background thread. Subscribes to Redis channels.
    Pattern: store:*:events
    """
    import redis as sync_redis

    while True:
        try:
            r = sync_redis.from_url(REDIS_URL)
            pubsub = r.pubsub()
            pubsub.psubscribe("store:*:events")
            state.set_connected(True)

            for message in pubsub.listen():
                if message["type"] == "pmessage":
                    try:
                        event = json.loads(message["data"])
                        state.update_from_event(event)
                    except Exception:
                        pass
        except Exception:
            state.set_connected(False)
            time.sleep(5)


def build_metrics_table(state: DashboardState) -> Table:
    """Build a Rich Table showing live metrics for all stores."""
    table = Table(
        title="Live Store Metrics",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Store ID", style="bold")
    table.add_column("Visitors Today")
    table.add_column("Conv %")
    table.add_column("Avg Dwell(s)")
    table.add_column("Queue")
    table.add_column("Abandonment %")
    table.add_column("Last Event")
    table.add_column("Events Seen")

    with state.lock:
        metrics_copy = dict(state.metrics)
        anomalies_copy = dict(state.anomalies)
        last_event_copy = dict(state.last_event)
        event_counts_copy = dict(state.event_counts)

    for store_id in STORE_IDS:
        m = metrics_copy.get(store_id, {})

        visitors = m.get("unique_visitors", "-")
        conv = m.get("conversion_rate", None)
        conv_str = f"{conv*100:.1f}%" if conv is not None else "-"

        avg_dwell = m.get("avg_dwell_per_zone", {})
        if avg_dwell:
            avg_ms = sum(avg_dwell.values()) / len(avg_dwell)
            dwell_str = f"{avg_ms/1000:.0f}s"
        else:
            dwell_str = "-"

        queue = m.get("current_queue_depth", "-")
        abandon = m.get("abandonment_rate", None)
        abandon_str = f"{abandon*100:.1f}%" if abandon is not None else "-"

        last = last_event_copy.get(store_id, "No events yet")
        if last != "No events yet":
            try:
                dt = datetime.fromisoformat(last)
                last = dt.strftime("%H:%M:%S")
            except Exception:
                pass

        count = event_counts_copy.get(store_id, 0)

        anomalies = anomalies_copy.get(store_id, [])
        severities = [a.get("severity") for a in anomalies]

        if "CRITICAL" in severities:
            row_style = "bold red"
        elif "WARN" in severities:
            row_style = "bold yellow"
        else:
            row_style = "bold green"

        table.add_row(
            store_id,
            str(visitors),
            conv_str,
            dwell_str,
            str(queue),
            abandon_str,
            str(last),
            str(count),
            style=row_style,
        )

    return table


def build_anomalies_panel(state: DashboardState) -> Panel:
    """Build a Rich Panel listing all active anomalies."""
    with state.lock:
        anomalies_copy = dict(state.anomalies)
        connected = state.connected

    lines = []

    if not connected:
        lines.append("[bold red]⚠ Feed Disconnected — last known values shown[/bold red]")
        lines.append("")

    has_anomalies = False
    for store_id in STORE_IDS:
        anomalies = anomalies_copy.get(store_id, [])
        for anomaly in anomalies:
            severity = anomaly.get("severity", "INFO")
            event_type = anomaly.get("event_type", "")
            message = anomaly.get("message", "")

            if severity == "CRITICAL":
                colour = "bold red"
                prefix = "[CRITICAL]"
            elif severity == "WARN":
                colour = "bold yellow"
                prefix = "[WARN]"
            else:
                colour = "bold blue"
                prefix = "[INFO]"

            lines.append(
                f"{prefix} {colour} {store_id} — {event_type}[/{colour}]"
            )
            if message:
                lines.append(f"  {colour}→ {message}[/{colour}]")
            lines.append("")

            has_anomalies = True

    if not has_anomalies and connected:
        lines.append("[bold green]✓ No active anomalies[/bold green]")

    content = "\n".join(lines) if lines else "[dim]Waiting for data...[/dim]"
    return Panel(
        content,
        title="Active Anomalies",
        border_style="cyan",
        box=box.ROUNDED,
    )


def build_header(state: DashboardState) -> Panel:
    """Build header panel showing connection status and timestamp."""
    with state.lock:
        connected = state.connected
        last_update = state.last_update

    status_colour = "bold green" if connected else "bold red"
    status_text = "● Connected" if connected else "● Disconnected"

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    header_text = Text()
    header_text.append("Store Intelligence — Live Dashboard\n", style="bold cyan")
    header_text.append(f"UTC: {now_utc}\n")
    header_text.append(f"Redis: {status_colour} {status_text}[/{status_colour}]\n")
    header_text.append(f"Last update: {last_update.strftime('%H:%M:%S')}")

    return Panel(
        header_text,
        border_style="cyan",
        box=box.ROUNDED,
    )


def build_layout(state: DashboardState) -> Layout:
    """Compose the full terminal layout."""
    layout = Layout()
    layout.split_column(
        Layout(build_header(state), size=6),
        Layout(build_metrics_table(state), ratio=6),
        Layout(build_anomalies_panel(state), ratio=3),
    )
    return layout


def main():
    """Entry point for the live dashboard."""
    print("Starting Store Intelligence Live Dashboard...")
    print(f"API: {API_URL}")
    print(f"Redis: {REDIS_URL}")
    print()

    state = DashboardState()

    t1 = threading.Thread(target=redis_subscriber, args=(state,),
                          daemon=True)
    t1.start()

    t2 = threading.Thread(target=fetch_metrics_loop, args=(state,),
                          daemon=True)
    t2.start()

    time.sleep(1)

    try:
        with Live(
            build_layout(state),
            refresh_per_second=REFRESH_RATE,
            screen=True,
        ) as live:
            while True:
                live.update(build_layout(state))
                time.sleep(1 / REFRESH_RATE)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")