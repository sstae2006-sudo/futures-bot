"""Interactive trade-decision review quiz.

Self-verify the trade journal by hand: one real trade at a time, shown
with its actual price candles, the strategy's stated reasoning for
entering, and the resulting PnL -- you judge it "right" or "wrong", and
the tool resurfaces what you marked wrong more often than what you
marked right. A Leitner-style weighted scheduler, the same underlying
idea Quizlet uses ("show me what I got wrong more"). Not a real ML
model -- a fixed weight table over your own past judgments, on purpose:
this is meant to focus your attention, not to grade the strategy for you.

Standalone by design: its own FastAPI app on its own port, its own local
SQLite judgments store (`logs/trade_review.db`). Never touches
research.db's schema or the main API's routes.

Trade + decision records are read directly out of `logs/decisions.jsonl`
(the same file `verify_decisions_journal.py` audits) rather than
research.db, so this reflects exactly what the engine actually logged.
Candles come from `market_data.db` via the normal `MarketDataStore`,
using this repo's own `config.yaml` (contract + resolution) so the chart
always matches what the strategy actually saw.

Building the byte-offset index requires one full scan of the journal the
first time; it's cached afterward (`logs/.trade_quiz_index.json`) so
every later launch starts instantly.

Usage:
    python tools/trade_quiz.py
    (opens a browser tab automatically at http://127.0.0.1:8765)
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from threading import Timer

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from futures_bot.config import load_settings  # noqa: E402
from futures_bot.market_data.store import MarketDataStore, default_db_path  # noqa: E402

JOURNAL_PATH = REPO_ROOT / "logs" / "decisions.jsonl"
INDEX_PATH = REPO_ROOT / "logs" / ".trade_quiz_index.json"
INDEX_VERSION = 3
DB_PATH = REPO_ROOT / "logs" / "trade_review.db"
PORT = 8765

#: How much more often a "wrong"-judged trade resurfaces than a
#: "right"-judged one, once every trade has been seen at least once.
WRONG_WEIGHT = 3.0
RIGHT_WEIGHT = 1.0

#: Chart window around the trade.
BARS_BEFORE_ENTRY = timedelta(minutes=150)
BARS_AFTER_EXIT = timedelta(minutes=25)


def _trade_key(obj: dict) -> str:
    # A `trade` record's exit time is logged under `timestamp` (see
    # journal.py::DecisionJournal.trade()) -- there is no separate
    # `exit_time` key on these records.
    return "|".join(
        str(obj.get(field))
        for field in ("entry_time", "timestamp", "side", "entry_price", "exit_price")
    )


def build_index() -> dict:
    """One-time (cached) pass over decisions.jsonl collecting:
      - every `trade` record's byte offset + length
      - every acted-on enter_long/enter_short `decision` record's byte
        offset + length, keyed by timestamp (a trade's entry_time matches
        the decision that caused it exactly, since both are logged off
        the same bar's `now`)
    so cards load instantly on every later request instead of re-scanning
    a multi-GB file per click."""
    if INDEX_PATH.exists():
        cached = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and cached.get("version") == INDEX_VERSION:
            return cached

    print(f"No usable cached index -- scanning {JOURNAL_PATH} once (only happens the first run "
          f"after this tool changes)...")
    trades: list[dict] = []
    decisions_by_ts: dict[str, dict] = {}
    offset = 0
    with JOURNAL_PATH.open("rb") as fh:
        for raw in fh:
            length = len(raw)
            stripped = raw.strip()
            if stripped:
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    obj = None
                if obj is not None:
                    record_type = obj.get("type")
                    if record_type == "trade":
                        trades.append({"offset": offset, "length": length, "key": _trade_key(obj)})
                    elif (
                        record_type == "decision"
                        and obj.get("acted")
                        and obj.get("action") in ("enter_long", "enter_short")
                    ):
                        decisions_by_ts[str(obj.get("timestamp"))] = {"offset": offset, "length": length}
            offset += length

    index = {"version": INDEX_VERSION, "trades": trades, "decisions_by_ts": decisions_by_ts}
    INDEX_PATH.write_text(json.dumps(index), encoding="utf-8")
    print(f"Indexed {len(trades):,} trades and {len(decisions_by_ts):,} entry decisions.")
    return index


def _load_record(entry: dict) -> dict:
    with JOURNAL_PATH.open("rb") as fh:
        fh.seek(entry["offset"])
        raw = fh.read(entry["length"])
    return json.loads(raw)


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS judgments (
            trade_key TEXT PRIMARY KEY,
            verdict TEXT NOT NULL,
            review_count INTEGER NOT NULL DEFAULT 0,
            judged_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _load_chart_settings() -> tuple[str, str]:
    """(product_code, resolution) -- falls back to this project's own
    documented defaults if config.yaml is missing/bad, same "never crash
    over a bad config file" posture as api/app.py."""
    config_path = REPO_ROOT / "config.yaml"
    try:
        settings = load_settings(config_path)
        return settings.contract, settings.research_server.resolution
    except Exception as exc:  # noqa: BLE001
        print(f"Could not load {config_path} for chart settings, using MES/5min defaults: {exc}")
        return "MES", "5min"


INDEX = build_index()
DB = init_db()
PRODUCT_CODE, RESOLUTION = _load_chart_settings()
app = FastAPI()


def _judgments() -> dict[str, dict]:
    rows = DB.execute("SELECT trade_key, verdict, review_count FROM judgments").fetchall()
    return {key: {"verdict": verdict, "review_count": count} for key, verdict, count in rows}


def _pick_next() -> dict | None:
    trades = INDEX["trades"]
    if not trades:
        return None
    judged = _judgments()
    unseen = [entry for entry in trades if entry["key"] not in judged]
    if unseen:
        return random.choice(unseen)

    weights = [
        WRONG_WEIGHT if judged[entry["key"]]["verdict"] == "wrong" else RIGHT_WEIGHT
        for entry in trades
    ]
    return random.choices(trades, weights=weights, k=1)[0]


def _fetch_bars(entry_time: str, exit_time: str) -> list[dict]:
    try:
        entry_dt = datetime.fromisoformat(entry_time)
        exit_dt = datetime.fromisoformat(exit_time)
    except (TypeError, ValueError):
        return []

    store = MarketDataStore(default_db_path())
    try:
        bars = store.fetch_bars(
            PRODUCT_CODE, RESOLUTION,
            start=entry_dt - BARS_BEFORE_ENTRY, end=exit_dt + BARS_AFTER_EXIT,
        )
    finally:
        store._conn.close()

    return [
        {"o": float(b.open), "h": float(b.high), "l": float(b.low), "c": float(b.close)}
        for b in bars
    ]


@app.get("/api/stats")
def stats():
    judged = _judgments()
    return {
        "total": len(INDEX["trades"]),
        "reviewed": len(judged),
        "right": sum(1 for j in judged.values() if j["verdict"] == "right"),
        "wrong": sum(1 for j in judged.values() if j["verdict"] == "wrong"),
    }


@app.get("/api/next-card")
def next_card():
    entry = _pick_next()
    if entry is None:
        return JSONResponse({"done": True})
    trade = _load_record(entry)
    judged = _judgments().get(entry["key"])

    decision_entry = INDEX["decisions_by_ts"].get(str(trade.get("entry_time")))
    decision = _load_record(decision_entry) if decision_entry else None

    bars = _fetch_bars(trade.get("entry_time"), trade.get("timestamp"))

    return {
        "key": entry["key"],
        "trade": trade,
        "reasoning": {
            "strategy_reason": decision.get("strategy_reason") if decision else None,
            "metadata": decision.get("metadata") if decision else {},
        },
        "bars": bars,
        "previous_verdict": judged["verdict"] if judged else None,
        "review_count": judged["review_count"] if judged else 0,
    }


class Judgment(BaseModel):
    key: str
    verdict: str


@app.post("/api/judge")
def judge(payload: Judgment):
    if payload.verdict not in ("right", "wrong"):
        return JSONResponse({"error": "verdict must be 'right' or 'wrong'"}, status_code=400)
    existing = DB.execute(
        "SELECT review_count FROM judgments WHERE trade_key = ?", (payload.key,)
    ).fetchone()
    count = (existing[0] if existing else 0) + 1
    DB.execute(
        """
        INSERT INTO judgments (trade_key, verdict, review_count, judged_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(trade_key) DO UPDATE SET
            verdict = excluded.verdict,
            review_count = excluded.review_count,
            judged_at = excluded.judged_at
        """,
        (payload.key, payload.verdict, count),
    )
    DB.commit()
    return {"ok": True}


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Trade Review Quiz</title>
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    max-width: 680px; margin: 40px auto; padding: 0 20px;
  }
  #stats { font-size: 14px; opacity: 0.7; margin-bottom: 20px; }
  .card {
    border: 1px solid currentColor; border-radius: 12px; padding: 24px;
    opacity: 0.95;
  }
  canvas { width: 100%; height: 220px; display: block; margin-bottom: 16px;
           border-radius: 8px; background: rgba(128,128,128,0.08); }
  .row { display: flex; justify-content: space-between; padding: 6px 0;
         border-bottom: 1px solid rgba(128,128,128,0.25); }
  .row:last-child { border-bottom: none; }
  .label { opacity: 0.65; }
  .pnl-pos { color: #16a34a; font-weight: 600; }
  .pnl-neg { color: #dc2626; font-weight: 600; }
  .prev-verdict { font-size: 13px; margin-bottom: 12px; opacity: 0.75; }
  .reasoning { margin-top: 16px; padding: 14px; border-radius: 8px;
               background: rgba(128,128,128,0.08); font-size: 14px; }
  .reasoning .why { font-style: italic; margin-bottom: 8px; }
  .meta-row { display: flex; justify-content: space-between; font-size: 13px; opacity: 0.8; padding: 2px 0; }
  .buttons { display: flex; gap: 12px; margin-top: 24px; }
  button {
    flex: 1; padding: 14px; font-size: 16px; border-radius: 8px; border: none;
    cursor: pointer; font-weight: 600;
  }
  .btn-wrong { background: #dc2626; color: white; }
  .btn-right { background: #16a34a; color: white; }
  .hint { text-align: center; font-size: 12px; opacity: 0.5; margin-top: 10px; }
  .done { text-align: center; opacity: 0.7; padding: 60px 0; }
</style>
</head>
<body>
<h2>Trade Review Quiz</h2>
<div id="stats">Loading...</div>
<div id="content">Loading...</div>

<script>
async function loadStats() {
  const s = await (await fetch('/api/stats')).json();
  document.getElementById('stats').textContent =
    `${s.reviewed} / ${s.total} reviewed  |  ${s.right} right, ${s.wrong} wrong`;
}

function fmtMoney(v) {
  const n = parseFloat(v);
  const cls = n >= 0 ? 'pnl-pos' : 'pnl-neg';
  return `<span class="${cls}">$${n.toFixed(2)}</span>`;
}

function fmtDuration(entryIso, exitIso) {
  const ms = new Date(exitIso) - new Date(entryIso);
  if (isNaN(ms)) return '?';
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `${mins}m`;
  return `${(mins / 60).toFixed(1)}h`;
}

function drawCandles(canvas, bars, entryPrice, exitPrice) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  if (!bars.length) {
    ctx.fillStyle = 'currentColor';
    ctx.font = '13px sans-serif';
    ctx.fillText('No candle data available for this window.', 10, h / 2);
    return;
  }

  const pad = 10;
  const lo = Math.min(...bars.map(b => b.l), entryPrice, exitPrice);
  const hi = Math.max(...bars.map(b => b.h), entryPrice, exitPrice);
  const range = (hi - lo) || 1;
  const y = (price) => h - pad - ((price - lo) / range) * (h - 2 * pad);

  const slot = w / bars.length;
  const bodyWidth = Math.max(2, slot * 0.6);

  bars.forEach((b, i) => {
    const cx = i * slot + slot / 2;
    const up = b.c >= b.o;
    ctx.strokeStyle = ctx.fillStyle = up ? '#16a34a' : '#dc2626';
    ctx.beginPath();
    ctx.moveTo(cx, y(b.h));
    ctx.lineTo(cx, y(b.l));
    ctx.stroke();
    const top = y(Math.max(b.o, b.c));
    const bot = y(Math.min(b.o, b.c));
    ctx.fillRect(cx - bodyWidth / 2, top, bodyWidth, Math.max(1, bot - top));
  });

  function marker(price, color, label) {
    const py = y(price);
    ctx.strokeStyle = color;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(0, py);
    ctx.lineTo(w, py);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.font = 'bold 11px sans-serif';
    ctx.fillText(label, w - 40, py - 4);
  }
  marker(entryPrice, '#2563eb', 'ENTRY');
  marker(exitPrice, '#f59e0b', 'EXIT');
}

let current = null;

async function loadCard() {
  const data = await (await fetch('/api/next-card')).json();
  const content = document.getElementById('content');
  if (data.done) {
    content.innerHTML = '<div class="done">Every trade has been reviewed at least once. ' +
      'Keep going and it\\'ll start resurfacing the ones you marked wrong.</div>';
    current = null;
    return;
  }
  current = data;
  const t = data.trade;
  const r = data.reasoning || {};
  const prevLine = data.previous_verdict
    ? `<div class="prev-verdict">Previously marked: <b>${data.previous_verdict.toUpperCase()}</b> (reviewed ${data.review_count}x)</div>`
    : '';

  const metaEntries = Object.entries(r.metadata || {});
  const metaHtml = metaEntries.length
    ? metaEntries.map(([k, v]) => `<div class="meta-row"><span>${k}</span><span>${v}</span></div>`).join('')
    : '';
  const reasoningHtml = (r.strategy_reason || metaEntries.length) ? `
    <div class="reasoning">
      <div class="why">"${r.strategy_reason || 'No reason recorded.'}"</div>
      ${metaHtml}
    </div>` : '';

  content.innerHTML = `
    <div class="card">
      ${prevLine}
      <canvas id="chart"></canvas>
      <div class="row"><span class="label">Side</span><span>${t.side} x${t.quantity}</span></div>
      <div class="row"><span class="label">Entry price</span><span>${t.entry_price}</span></div>
      <div class="row"><span class="label">Exit price</span><span>${t.exit_price}</span></div>
      <div class="row"><span class="label">Duration</span><span>${fmtDuration(t.entry_time, t.timestamp)}</span></div>
      <div class="row"><span class="label">Exit reason</span><span>${t.exit_reason}</span></div>
      <div class="row"><span class="label">Gross PnL</span><span>${fmtMoney(t.gross_pnl)}</span></div>
      <div class="row"><span class="label">Commission</span><span>$${parseFloat(t.commission).toFixed(2)}</span></div>
      <div class="row"><span class="label">Net PnL</span><span>${fmtMoney(t.net_pnl)}</span></div>
      <div class="row"><span class="label">Session PnL after</span><span>${fmtMoney(t.session_pnl)}</span></div>
      ${reasoningHtml}
    </div>
    <div class="buttons">
      <button class="btn-wrong" onclick="judge('wrong')">✗ Wrong (←)</button>
      <button class="btn-right" onclick="judge('right')">✓ Right (→)</button>
    </div>
    <div class="hint">Judge whether this trade's entry/exit/PnL look correct and sane -- not whether it made money.</div>
  `;
  drawCandles(document.getElementById('chart'), data.bars || [], parseFloat(t.entry_price), parseFloat(t.exit_price));
}

async function judge(verdict) {
  if (!current) return;
  await fetch('/api/judge', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key: current.key, verdict}),
  });
  await loadStats();
  await loadCard();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft') judge('wrong');
  if (e.key === 'ArrowRight') judge('right');
});

loadStats();
loadCard();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return _PAGE


if __name__ == "__main__":
    Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    print(f"Trade review quiz running at http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
