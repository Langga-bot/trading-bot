import os
import logging
from datetime import datetime, date
from typing import List, Optional
from contextlib import contextmanager

from config import DATABASE_CONFIG

logger = logging.getLogger(__name__)

PG_URL = os.getenv("DATABASE_URL", DATABASE_CONFIG.get("pg_url", ""))

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.extensions

    _PSYCOPG2_OK = bool(PG_URL)

    if _PSYCOPG2_OK:
        try:
            import numpy as np

            def _adapt_numpy_float(val):
                return psycopg2.extensions.AsIs(repr(float(val)))

            def _adapt_numpy_int(val):
                return psycopg2.extensions.AsIs(repr(int(val)))

            def _adapt_numpy_bool(val):
                return psycopg2.extensions.AsIs(repr(bool(val)))

            psycopg2.extensions.register_adapter(np.float64,  _adapt_numpy_float)
            psycopg2.extensions.register_adapter(np.float32,  _adapt_numpy_float)
            psycopg2.extensions.register_adapter(np.int64,    _adapt_numpy_int)
            psycopg2.extensions.register_adapter(np.int32,    _adapt_numpy_int)
            psycopg2.extensions.register_adapter(np.bool_,    _adapt_numpy_bool)
        except ImportError:
            pass
except ImportError:
    _PSYCOPG2_OK = False

USE_POSTGRES = _PSYCOPG2_OK and bool(PG_URL)

if USE_POSTGRES:
    logger.info("[DB] Mode: PostgreSQL (Railway/cloud)")
else:
    import sqlite3
    logger.info("[DB] Mode: SQLite lokal")


class Database:
    """
    Database wrapper yang mendukung SQLite dan PostgreSQL.
    Deteksi otomatis berdasarkan DATABASE_URL environment variable.
    """

    def __init__(self):
        if USE_POSTGRES:
            self._pg_url = PG_URL.replace("postgres://", "postgresql://", 1)
            self.db_type = "postgresql"
        else:
            self.db_type  = "sqlite"
            self._db_path = DATABASE_CONFIG.get("sqlite_path", "data/trades.db")
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)

        self._init_tables()
        logger.info(f"[DB] Database siap ({self.db_type})")

    @contextmanager
    def _get_conn(self):
        """Context manager koneksi — bekerja untuk SQLite dan PostgreSQL."""
        if USE_POSTGRES:
            conn = psycopg2.connect(self._pg_url)
            conn.autocommit = False
            try:
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"[DB] PG transaction error: {e}")
                raise
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(self._db_path, timeout=15)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"[DB] SQLite transaction error: {e}")
                raise
            finally:
                conn.close()

    def _execute(self, conn, sql: str, params=None):
        """Eksekusi SQL dengan konversi placeholder ? -> %s untuk PostgreSQL."""
        if USE_POSTGRES:
            sql = sql.replace("?", "%s")
            sql = sql.replace("datetime('now')", "NOW()")
            sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            sql = sql.replace("AUTOINCREMENT", "")
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cur = conn.cursor()
        cur.execute(sql, params or ())
        return cur

    def _fetchall(self, cur) -> List[dict]:
        rows = cur.fetchall()
        if USE_POSTGRES:
            return [dict(r) for r in rows]
        return [dict(r) for r in rows]

    def _fetchone(self, cur) -> Optional[dict]:
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def _init_tables(self):
        """Buat semua tabel jika belum ada."""
        if USE_POSTGRES:
            self._init_tables_pg()
        else:
            self._init_tables_sqlite()

    def _init_tables_sqlite(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair         TEXT NOT NULL,
                    trade_type   TEXT NOT NULL,
                    entry_price  REAL,
                    exit_price   REAL,
                    coin_amount  REAL,
                    idr_invested REAL,
                    idr_received REAL,
                    pnl_idr      REAL DEFAULT 0,
                    pnl_pct      REAL DEFAULT 0,
                    strategy     TEXT,
                    reason       TEXT,
                    duration_min INTEGER DEFAULT 0,
                    order_id     TEXT,
                    dry_run      INTEGER DEFAULT 0,
                    entry_time   TEXT,
                    exit_time    TEXT,
                    created_at   TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS open_positions (
                    pair                TEXT PRIMARY KEY,
                    entry_price         REAL NOT NULL,
                    coin_amount         REAL NOT NULL,
                    idr_invested        REAL NOT NULL,
                    slot_size           REAL DEFAULT 0,
                    stop_loss           REAL NOT NULL,
                    take_profit         REAL NOT NULL,
                    trailing_stop_price REAL DEFAULT 0,
                    highest_price       REAL DEFAULT 0,
                    order_id            TEXT DEFAULT '',
                    entry_time          TEXT,
                    updated_at          TEXT DEFAULT (datetime('now'))
                );

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

                CREATE TABLE IF NOT EXISTS daily_summary (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date     TEXT UNIQUE,
                    total_trades   INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    losing_trades  INTEGER DEFAULT 0,
                    gross_profit   REAL DEFAULT 0,
                    gross_loss     REAL DEFAULT 0,
                    net_pnl        REAL DEFAULT 0,
                    winrate        REAL DEFAULT 0,
                    best_trade     REAL DEFAULT 0,
                    worst_trade    REAL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS error_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    level      TEXT,
                    message    TEXT,
                    context    TEXT,
                    logged_at  TEXT DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair);
                CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(created_at);
            """)

    def _init_tables_pg(self):
        with self._get_conn() as conn:
            cur = conn.cursor()
            stmts = [
                """CREATE TABLE IF NOT EXISTS trades (
                    id           SERIAL PRIMARY KEY,
                    pair         TEXT NOT NULL,
                    trade_type   TEXT NOT NULL,
                    entry_price  DOUBLE PRECISION,
                    exit_price   DOUBLE PRECISION,
                    coin_amount  DOUBLE PRECISION,
                    idr_invested DOUBLE PRECISION,
                    idr_received DOUBLE PRECISION,
                    pnl_idr      DOUBLE PRECISION DEFAULT 0,
                    pnl_pct      DOUBLE PRECISION DEFAULT 0,
                    strategy     TEXT,
                    reason       TEXT,
                    duration_min INTEGER DEFAULT 0,
                    order_id     TEXT,
                    dry_run      INTEGER DEFAULT 0,
                    entry_time   TEXT,
                    exit_time    TEXT,
                    created_at   TIMESTAMP DEFAULT NOW()
                )""",
                """CREATE TABLE IF NOT EXISTS open_positions (
                    pair                TEXT PRIMARY KEY,
                    entry_price         DOUBLE PRECISION NOT NULL,
                    coin_amount         DOUBLE PRECISION NOT NULL,
                    idr_invested        DOUBLE PRECISION NOT NULL,
                    slot_size           DOUBLE PRECISION DEFAULT 0,
                    stop_loss           DOUBLE PRECISION NOT NULL,
                    take_profit         DOUBLE PRECISION NOT NULL,
                    trailing_stop_price DOUBLE PRECISION DEFAULT 0,
                    highest_price       DOUBLE PRECISION DEFAULT 0,
                    order_id            TEXT DEFAULT '',
                    entry_time          TEXT,
                    updated_at          TIMESTAMP DEFAULT NOW()
                )""",
                """CREATE TABLE IF NOT EXISTS market_snapshots (
                    id            SERIAL PRIMARY KEY,
                    pair          TEXT NOT NULL,
                    price         DOUBLE PRECISION,
                    ema_fast      DOUBLE PRECISION,
                    ema_slow      DOUBLE PRECISION,
                    rsi           DOUBLE PRECISION,
                    macd_hist     DOUBLE PRECISION,
                    bb_signal     TEXT,
                    volume_spike  INTEGER,
                    trend         TEXT,
                    buy_score     INTEGER,
                    sell_score    INTEGER,
                    signal        TEXT,
                    confidence    DOUBLE PRECISION,
                    snapshot_time TIMESTAMP DEFAULT NOW()
                )""",
                """CREATE TABLE IF NOT EXISTS daily_summary (
                    id             SERIAL PRIMARY KEY,
                    trade_date     TEXT UNIQUE,
                    total_trades   INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    losing_trades  INTEGER DEFAULT 0,
                    gross_profit   DOUBLE PRECISION DEFAULT 0,
                    gross_loss     DOUBLE PRECISION DEFAULT 0,
                    net_pnl        DOUBLE PRECISION DEFAULT 0,
                    winrate        DOUBLE PRECISION DEFAULT 0,
                    best_trade     DOUBLE PRECISION DEFAULT 0,
                    worst_trade    DOUBLE PRECISION DEFAULT 0
                )""",
                """CREATE TABLE IF NOT EXISTS error_log (
                    id        SERIAL PRIMARY KEY,
                    level     TEXT,
                    message   TEXT,
                    context   TEXT,
                    logged_at TIMESTAMP DEFAULT NOW()
                )""",
                "CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair)",
                "CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(created_at)",
            ]
            for stmt in stmts:
                cur.execute(stmt)

    def _upsert_open_position_sql(self) -> str:
        if USE_POSTGRES:
            return """
                INSERT INTO open_positions (
                    pair, entry_price, coin_amount, idr_invested, slot_size,
                    stop_loss, take_profit, trailing_stop_price, highest_price,
                    order_id, entry_time, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (pair) DO UPDATE SET
                    entry_price         = EXCLUDED.entry_price,
                    coin_amount         = EXCLUDED.coin_amount,
                    idr_invested        = EXCLUDED.idr_invested,
                    slot_size           = EXCLUDED.slot_size,
                    stop_loss           = EXCLUDED.stop_loss,
                    take_profit         = EXCLUDED.take_profit,
                    trailing_stop_price = EXCLUDED.trailing_stop_price,
                    highest_price       = EXCLUDED.highest_price,
                    order_id            = EXCLUDED.order_id,
                    entry_time          = EXCLUDED.entry_time,
                    updated_at          = NOW()
            """
        else:
            return """
                INSERT INTO open_positions (
                    pair, entry_price, coin_amount, idr_invested, slot_size,
                    stop_loss, take_profit, trailing_stop_price, highest_price,
                    order_id, entry_time, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                ON CONFLICT(pair) DO UPDATE SET
                    entry_price         = excluded.entry_price,
                    coin_amount         = excluded.coin_amount,
                    idr_invested        = excluded.idr_invested,
                    slot_size           = excluded.slot_size,
                    stop_loss           = excluded.stop_loss,
                    take_profit         = excluded.take_profit,
                    trailing_stop_price = excluded.trailing_stop_price,
                    highest_price       = excluded.highest_price,
                    order_id            = excluded.order_id,
                    entry_time          = excluded.entry_time,
                    updated_at          = datetime('now')
            """

    def save_open_position(self, pair: str, pos) -> None:
        """Simpan/update posisi terbuka — persistent di DB."""
        f   = self._to_py
        sql = self._upsert_open_position_sql()
        params = (
            str(pair),
            f(pos.entry_price),
            f(pos.coin_amount),
            f(pos.idr_invested),
            f(getattr(pos, "slot_size", 0) or 0),
            f(pos.stop_loss),
            f(pos.take_profit),
            f(pos.trailing_stop_price),
            f(pos.highest_price),
            str(pos.order_id or ""),
            str(pos.entry_time),
        )
        try:
            with self._get_conn() as conn:
                conn.cursor().execute(sql, params)
        except Exception as e:
            logger.error(f"[DB] save_open_position error: {e}")

    def delete_open_position(self, pair: str) -> None:
        """Hapus posisi setelah ditutup."""
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                if USE_POSTGRES:
                    cur.execute("DELETE FROM open_positions WHERE pair = %s", (pair,))
                else:
                    cur.execute("DELETE FROM open_positions WHERE pair = ?", (pair,))
        except Exception as e:
            logger.error(f"[DB] delete_open_position error: {e}")

    def load_open_positions(self) -> List[dict]:
        """Load semua posisi terbuka — dipanggil saat bot start."""
        try:
            with self._get_conn() as conn:
                if USE_POSTGRES:
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                    cur.execute("SELECT * FROM open_positions ORDER BY entry_time")
                    return [dict(r) for r in cur.fetchall()]
                else:
                    cur = conn.execute("SELECT * FROM open_positions ORDER BY entry_time")
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"[DB] load_open_positions error: {e}")
            return []

    def save_trade(self, trade: dict) -> int:
        """Simpan record trade — konversi semua nilai ke Python native types."""
        f = self._to_py
        if USE_POSTGRES:
            sql = """
                INSERT INTO trades (
                    pair, trade_type, entry_price, exit_price,
                    coin_amount, idr_invested, idr_received,
                    pnl_idr, pnl_pct, strategy, reason,
                    duration_min, order_id, dry_run, entry_time, exit_time
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """
        else:
            sql = """
                INSERT INTO trades (
                    pair, trade_type, entry_price, exit_price,
                    coin_amount, idr_invested, idr_received,
                    pnl_idr, pnl_pct, strategy, reason,
                    duration_min, order_id, dry_run, entry_time, exit_time
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
        params = (
            str(trade.get("pair", "")),
            str(trade.get("trade_type", "")),
            f(trade.get("entry_price", 0)) or 0.0,
            f(trade.get("exit_price", 0)) or 0.0,
            f(trade.get("coin_amount", 0)) or 0.0,
            f(trade.get("idr_invested", 0)) or 0.0,
            f(trade.get("idr_received", 0)) or 0.0,
            f(trade.get("pnl_idr", 0)) or 0.0,
            f(trade.get("pnl_pct", 0)) or 0.0,
            str(trade.get("strategy", "") or ""),
            str(trade.get("reason", "") or ""),
            int(f(trade.get("duration_min", 0)) or 0),
            str(trade.get("order_id", "") or ""),
            1 if trade.get("dry_run") else 0,
            str(trade.get("entry_time", "")),
            str(trade.get("exit_time", "")),
        )
        trade_id = 0
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                if USE_POSTGRES:
                    trade_id = cur.fetchone()[0]
                else:
                    trade_id = cur.lastrowid
        except Exception as e:
            logger.error(f"[DB] save_trade error: {e}")
        self._update_daily_summary()
        return trade_id

    @staticmethod
    def _to_py(v):
        """
        Konversi APAPUN ke Python native type.
        Aman untuk PostgreSQL — tidak ada numpy types, tidak ada NaN/Inf.
        """
        if v is None:
            return None

        try:
            import numpy as np
            if isinstance(v, np.generic):
                v = v.item()
        except ImportError:
            pass

        if isinstance(v, float):
            import math
            if math.isnan(v) or math.isinf(v):
                return 0.0
            return float(v)
        if isinstance(v, int):
            return int(v)
        if isinstance(v, bool):
            return bool(v)

        try:
            return float(v)
        except (TypeError, ValueError):
            pass
        return v

    def save_snapshot(self, pair: str, price: float, ind, decision) -> None:
        """Simpan snapshot market — konversi numpy types sebelum insert."""
        try:
            f = self._to_py
            if USE_POSTGRES:
                sql = """INSERT INTO market_snapshots
                    (pair,price,ema_fast,ema_slow,rsi,macd_hist,
                     bb_signal,volume_spike,trend,buy_score,sell_score,signal,confidence)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
            else:
                sql = """INSERT INTO market_snapshots
                    (pair,price,ema_fast,ema_slow,rsi,macd_hist,
                     bb_signal,volume_spike,trend,buy_score,sell_score,signal,confidence)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
            params = (
                str(pair),
                f(price),
                f(getattr(ind, "ema_fast", 0)),
                f(getattr(ind, "ema_slow", 0)),
                f(getattr(ind, "rsi", 0)),
                f(getattr(ind, "macd_hist", 0)),
                str(getattr(ind, "bb_signal", "") or ""),
                1 if getattr(ind, "volume_spike", False) else 0,
                str(getattr(ind, "trend", "") or ""),
                int(f(getattr(ind, "buy_score", 0)) or 0),
                int(f(getattr(ind, "sell_score", 0)) or 0),
                str(decision.action.value if decision else "HOLD"),
                f(getattr(decision, "confidence", 0)),
            )
            with self._get_conn() as conn:
                conn.cursor().execute(sql, params)
        except Exception as e:
            logger.debug(f"[DB] save_snapshot error: {e}")

    def log_error(self, level: str, message: str, context: str = "") -> None:
        try:
            if USE_POSTGRES:
                sql = "INSERT INTO error_log (level,message,context) VALUES (%s,%s,%s)"
            else:
                sql = "INSERT INTO error_log (level,message,context) VALUES (?,?,?)"
            with self._get_conn() as conn:
                conn.cursor().execute(sql, (level, message, context))
        except Exception:
            pass

    def get_trade_history(self, pair: str = "", limit: int = 50) -> List[dict]:
        try:
            with self._get_conn() as conn:
                if USE_POSTGRES:
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                    if pair:
                        cur.execute(
                            "SELECT * FROM trades WHERE pair=%s ORDER BY created_at DESC LIMIT %s",
                            (pair, limit)
                        )
                    else:
                        cur.execute(
                            "SELECT * FROM trades ORDER BY created_at DESC LIMIT %s",
                            (limit,)
                        )
                else:
                    if pair:
                        cur = conn.execute(
                            "SELECT * FROM trades WHERE pair=? ORDER BY created_at DESC LIMIT ?",
                            (pair, limit)
                        )
                    else:
                        cur = conn.execute(
                            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                            (limit,)
                        )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"[DB] get_trade_history error: {e}")
            return []

    def get_daily_stats(self, days: int = 7) -> List[dict]:
        try:
            with self._get_conn() as conn:
                if USE_POSTGRES:
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                    cur.execute(
                        "SELECT * FROM daily_summary ORDER BY trade_date DESC LIMIT %s",
                        (days,)
                    )
                else:
                    cur = conn.execute(
                        "SELECT * FROM daily_summary ORDER BY trade_date DESC LIMIT ?",
                        (days,)
                    )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"[DB] get_daily_stats error: {e}")
            return []

    def get_overall_stats(self) -> dict:
        try:
            with self._get_conn() as conn:
                if USE_POSTGRES:
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                    cur.execute("""
                        SELECT
                            COUNT(*) AS total_trades,
                            SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) AS wins,
                            SUM(CASE WHEN pnl_idr < 0 THEN 1 ELSE 0 END) AS losses,
                            SUM(pnl_idr) AS net_pnl,
                            AVG(pnl_pct) AS avg_pnl_pct,
                            MAX(pnl_idr) AS best_trade,
                            MIN(pnl_idr) AS worst_trade,
                            AVG(duration_min) AS avg_duration_min
                        FROM trades WHERE dry_run = 0
                    """)
                else:
                    cur = conn.execute("""
                        SELECT
                            COUNT(*) AS total_trades,
                            SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) AS wins,
                            SUM(CASE WHEN pnl_idr < 0 THEN 1 ELSE 0 END) AS losses,
                            SUM(pnl_idr) AS net_pnl,
                            AVG(pnl_pct) AS avg_pnl_pct,
                            MAX(pnl_idr) AS best_trade,
                            MIN(pnl_idr) AS worst_trade,
                            AVG(duration_min) AS avg_duration_min
                        FROM trades WHERE dry_run = 0
                    """)
                row = cur.fetchone()
            if row:
                d = dict(row)
                total = d.get("total_trades") or 1
                d["winrate"] = (d.get("wins") or 0) / total * 100
                return d
        except Exception as e:
            logger.error(f"[DB] get_overall_stats error: {e}")
        return {}

    def _update_daily_summary(self):
        today = date.today().isoformat()
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                if USE_POSTGRES:
                    cur.execute(
                        "INSERT INTO daily_summary (trade_date) VALUES (%s) ON CONFLICT DO NOTHING",
                        (today,)
                    )
                    cur.execute("""
                        SELECT COUNT(*) AS total,
                               SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) AS wins,
                               SUM(CASE WHEN pnl_idr < 0 THEN 1 ELSE 0 END) AS losses,
                               SUM(CASE WHEN pnl_idr > 0 THEN pnl_idr ELSE 0 END) AS gp,
                               SUM(CASE WHEN pnl_idr < 0 THEN pnl_idr ELSE 0 END) AS gl,
                               SUM(pnl_idr) AS net,
                               MAX(pnl_idr) AS best,
                               MIN(pnl_idr) AS worst
                        FROM trades WHERE DATE(created_at::timestamp) = %s
                    """, (today,))
                else:
                    cur.execute(
                        "INSERT INTO daily_summary (trade_date) VALUES (?) ON CONFLICT(trade_date) DO NOTHING",
                        (today,)
                    )
                    cur.execute("""
                        SELECT COUNT(*) AS total,
                               SUM(CASE WHEN pnl_idr > 0 THEN 1 ELSE 0 END) AS wins,
                               SUM(CASE WHEN pnl_idr < 0 THEN 1 ELSE 0 END) AS losses,
                               SUM(CASE WHEN pnl_idr > 0 THEN pnl_idr ELSE 0 END) AS gp,
                               SUM(CASE WHEN pnl_idr < 0 THEN pnl_idr ELSE 0 END) AS gl,
                               SUM(pnl_idr) AS net,
                               MAX(pnl_idr) AS best,
                               MIN(pnl_idr) AS worst
                        FROM trades WHERE DATE(created_at) = ?
                    """, (today,))

                row = cur.fetchone()
                if row:
                    row = dict(row)
                    if row.get("total"):
                        wins  = row.get("wins") or 0
                        total = row.get("total") or 1
                        if USE_POSTGRES:
                            cur.execute("""
                                UPDATE daily_summary
                                SET total_trades=%s, winning_trades=%s, losing_trades=%s,
                                    gross_profit=%s, gross_loss=%s, net_pnl=%s,
                                    winrate=%s, best_trade=%s, worst_trade=%s
                                WHERE trade_date=%s
                            """, (
                                row["total"], wins, row.get("losses") or 0,
                                row.get("gp") or 0, row.get("gl") or 0, row.get("net") or 0,
                                wins / total * 100,
                                row.get("best") or 0, row.get("worst") or 0,
                                today,
                            ))
                        else:
                            cur.execute("""
                                UPDATE daily_summary
                                SET total_trades=?, winning_trades=?, losing_trades=?,
                                    gross_profit=?, gross_loss=?, net_pnl=?,
                                    winrate=?, best_trade=?, worst_trade=?
                                WHERE trade_date=?
                            """, (
                                row["total"], wins, row.get("losses") or 0,
                                row.get("gp") or 0, row.get("gl") or 0, row.get("net") or 0,
                                wins / total * 100,
                                row.get("best") or 0, row.get("worst") or 0,
                                today,
                            ))
        except Exception as e:
            logger.debug(f"[DB] _update_daily_summary error: {e}")
