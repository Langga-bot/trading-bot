import sqlite3
import logging
import os
from datetime import datetime, date
from typing import List, Optional, Dict
from contextlib import contextmanager

from config import DATABASE_CONFIG

logger = logging.getLogger(__name__)


class Database:

    def __init__(self):
        self.db_type = DATABASE_CONFIG["type"]
        self.db_path = DATABASE_CONFIG["sqlite_path"]
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_tables()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"[DB] Transaction error: {e}")
            raise
        finally:
            conn.close()

    def _init_tables(self):
        with self._get_conn() as conn:
            conn.executescript("""
                -- Riwayat semua trade
                CREATE TABLE IF NOT EXISTS trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair          TEXT NOT NULL,
                    trade_type    TEXT NOT NULL,       -- BUY / SELL
                    entry_price   REAL,
                    exit_price    REAL,
                    coin_amount   REAL,
                    idr_invested  REAL,
                    idr_received  REAL,
                    pnl_idr       REAL DEFAULT 0,
                    pnl_pct       REAL DEFAULT 0,
                    strategy      TEXT,
                    reason        TEXT,
                    duration_min  INTEGER DEFAULT 0,
                    order_id      TEXT,
                    dry_run       INTEGER DEFAULT 0,
                    entry_time    TEXT,
                    exit_time     TEXT,
                    created_at    TEXT DEFAULT (datetime('now'))
                );

                -- Snapshot indikator per analisis
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair          TEXT NOT NULL,
                    price         REAL,
                    ema_fast      REAL,
                    ema_slow      REAL,
                    rsi           REAL,
                    macd_hist     REAL,
                    bb_signal     TEXT,
                    volume_spike  INTEGER,
                    trend         TEXT,
                    buy_score     INTEGER,
                    sell_score    INTEGER,
                    signal        TEXT,
                    confidence    REAL,
                    snapshot_time TEXT DEFAULT (datetime('now'))
                );

                -- Summary harian
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date      TEXT UNIQUE,
                    total_trades    INTEGER DEFAULT 0,
                    winning_trades  INTEGER DEFAULT 0,
                    losing_trades   INTEGER DEFAULT 0,
                    gross_profit    REAL DEFAULT 0,
                    gross_loss      REAL DEFAULT 0,
                    net_pnl         REAL DEFAULT 0,
                    winrate         REAL DEFAULT 0,
                    best_trade      REAL DEFAULT 0,
                    worst_trade     REAL DEFAULT 0
                );

                -- Log error
                CREATE TABLE IF NOT EXISTS error_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    level       TEXT,
                    message     TEXT,
                    context     TEXT,
                    logged_at   TEXT DEFAULT (datetime('now'))
                );

                -- Indeks untuk performa query
                CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair);
                CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(created_at);
                CREATE INDEX IF NOT EXISTS idx_snapshots_pair ON market_snapshots(pair);
            """)
        logger.info(f"[DB] Database siap: {self.db_path}")


    def save_trade(self, trade: dict) -> int:
        with self._get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO trades (
                    pair, trade_type, entry_price, exit_price,
                    coin_amount, idr_invested, idr_received,
                    pnl_idr, pnl_pct, strategy, reason,
                    duration_min, order_id, dry_run,
                    entry_time, exit_time
                ) VALUES (
                    :pair, :trade_type, :entry_price, :exit_price,
                    :coin_amount, :idr_invested, :idr_received,
                    :pnl_idr, :pnl_pct, :strategy, :reason,
                    :duration_min, :order_id, :dry_run,
                    :entry_time, :exit_time
                )
            """, {
                "pair":         trade.get("pair", ""),
                "trade_type":   trade.get("trade_type", ""),
                "entry_price":  trade.get("entry_price", 0),
                "exit_price":   trade.get("exit_price", 0),
                "coin_amount":  trade.get("coin_amount", 0),
                "idr_invested": trade.get("idr_invested", 0),
                "idr_received": trade.get("idr_received", 0),
                "pnl_idr":      trade.get("pnl_idr", 0),
                "pnl_pct":      trade.get("pnl_pct", 0),
                "strategy":     trade.get("strategy", ""),
                "reason":       trade.get("reason", ""),
                "duration_min": trade.get("duration_min", 0),
                "order_id":     trade.get("order_id", ""),
                "dry_run":      1 if trade.get("dry_run") else 0,
                "entry_time":   str(trade.get("entry_time", "")),
                "exit_time":    str(trade.get("exit_time", "")),
            })
            trade_id = cur.lastrowid
        self._update_daily_summary()
        return trade_id

    def save_snapshot(self, pair: str, price: float, ind, decision) -> None:
        try:
            with self._get_conn() as conn:
                conn.execute("""
                    INSERT INTO market_snapshots (
                        pair, price, ema_fast, ema_slow, rsi, macd_hist,
                        bb_signal, volume_spike, trend, buy_score, sell_score,
                        signal, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pair, price,
                    getattr(ind, "ema_fast", 0),
                    getattr(ind, "ema_slow", 0),
                    getattr(ind, "rsi", 0),
                    getattr(ind, "macd_hist", 0),
                    getattr(ind, "bb_signal", ""),
                    1 if getattr(ind, "volume_spike", False) else 0,
                    getattr(ind, "trend", ""),
                    getattr(ind, "buy_score", 0),
                    getattr(ind, "sell_score", 0),
                    decision.action.value if decision else "HOLD",
                    getattr(decision, "confidence", 0),
                ))
        except Exception as e:
            logger.warning(f"[DB] save_snapshot error: {e}")

    def log_error(self, level: str, message: str, context: str = "") -> None:
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO error_log (level, message, context) VALUES (?, ?, ?)",
                    (level, message, context),
                )
        except Exception:
            pass


    def get_trade_history(self, pair: str = "", limit: int = 50) -> List[dict]:
        with self._get_conn() as conn:
            if pair:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE pair=? ORDER BY created_at DESC LIMIT ?",
                    (pair, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_daily_stats(self, days: int = 7) -> List[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_summary ORDER BY trade_date DESC LIMIT ?",
                (days,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_overall_stats(self) -> dict:
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*)                           AS total_trades,
                    SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN pnl_idr < 0 THEN 1 ELSE 0 END) AS losses,
                    SUM(pnl_idr)                       AS net_pnl,
                    AVG(pnl_pct)                       AS avg_pnl_pct,
                    MAX(pnl_idr)                       AS best_trade,
                    MIN(pnl_idr)                       AS worst_trade,
                    AVG(duration_min)                  AS avg_duration_min
                FROM trades
                WHERE dry_run = 0
            """).fetchone()
        if row:
            d = dict(row)
            total = d["total_trades"] or 1
            d["winrate"] = (d["wins"] or 0) / total * 100
            return d
        return {}


    def _update_daily_summary(self):
        today = date.today().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO daily_summary (trade_date) VALUES (?)
                ON CONFLICT(trade_date) DO NOTHING
            """, (today,))

            row = conn.execute("""
                SELECT
                    COUNT(*)                                   AS total,
                    SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN pnl_idr < 0 THEN 1 ELSE 0 END) AS losses,
                    SUM(CASE WHEN pnl_idr > 0 THEN pnl_idr ELSE 0 END) AS gp,
                    SUM(CASE WHEN pnl_idr < 0 THEN pnl_idr ELSE 0 END) AS gl,
                    SUM(pnl_idr) AS net,
                    MAX(pnl_idr) AS best,
                    MIN(pnl_idr) AS worst
                FROM trades
                WHERE DATE(created_at) = ?
            """, (today,)).fetchone()

            if row and row["total"]:
                wins = row["wins"] or 0
                total = row["total"] or 1
                conn.execute("""
                    UPDATE daily_summary
                    SET total_trades=?, winning_trades=?, losing_trades=?,
                        gross_profit=?, gross_loss=?, net_pnl=?,
                        winrate=?, best_trade=?, worst_trade=?
                    WHERE trade_date=?
                """, (
                    row["total"], wins, row["losses"] or 0,
                    row["gp"] or 0, row["gl"] or 0, row["net"] or 0,
                    wins / total * 100,
                    row["best"] or 0, row["worst"] or 0,
                    today,
                ))
