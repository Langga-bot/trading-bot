import sys
import time
import signal
import logging
import logging.handlers
import argparse
import os
import json
import threading
import requests
from datetime import datetime, time as dt_time
from dotenv import load_dotenv

load_dotenv()

def setup_logging(level: str = "INFO") -> None:
    os.makedirs("logs", exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            ch = logging.StreamHandler(sys.stdout)
        except Exception:
            import io
            safe_out = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
            ch = logging.StreamHandler(safe_out)
    else:
        ch = logging.StreamHandler(sys.stdout)

    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = logging.handlers.RotatingFileHandler(
        "logs/bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

setup_logging()
logger = logging.getLogger("main")

from config import (
    TRADING_PAIRS, BOT_CONFIG, RISK_CONFIG, LOG_CONFIG, BACKTEST_CONFIG,
    TELEGRAM_CONFIG,
)
from api             import IndodaxAPI, IndodaxAPIError
from strategy        import StrategyEngine, Signal
from risk_management import RiskManager
from database        import Database
from notifier        import TelegramNotifier, E, LINE, DLINE


class TelegramCommandThread(threading.Thread):

    PAUSE_FLAG = "data/.bot_paused"

    def __init__(self, bot_ref):
        super().__init__(daemon=True, name="TelegramCMD")
        self.bot      = bot_ref
        self.token    = TELEGRAM_CONFIG.get("bot_token", "").strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.running  = True
        self.log      = logging.getLogger("telegram_cmd")

        raw_chat = str(TELEGRAM_CONFIG.get("chat_id", "")).strip().strip('"').strip("'")
        self.allowed_ids = {c.strip() for c in raw_chat.split(",") if c.strip()}
        self.chat_id = next(iter(self.allowed_ids), "")
        self.log.debug(f"[CMD] Allowed chat IDs: {self.allowed_ids}")


    def _get(self, method: str, params: dict = None, timeout: int = 40) -> dict:
        try:
            r = requests.get(f"{self.base_url}/{method}", params=params, timeout=timeout)
            return r.json()
        except requests.exceptions.ReadTimeout:
            return {"ok": True, "result": []}
        except requests.exceptions.ConnectionError:
            return {}
        except Exception as e:
            self.log.debug(f"TG GET: {e}")
            return {}

    def _send(self, text: str, reply_id: int = None, to_chat: str = None) -> bool:
        target = to_chat or self.chat_id
        if not target:
            return False
        payload = {
            "chat_id":                  target,
            "text":                     text,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        }
        if reply_id:
            payload["reply_to_message_id"] = reply_id
        try:
            r = requests.post(
                f"{self.base_url}/sendMessage",
                json=payload, timeout=15
            )
            return r.status_code == 200 and r.json().get("ok", False)
        except Exception as e:
            self.log.debug(f"TG send: {e}")
            return False

    def _now(self) -> str:
        return datetime.now().strftime("%d %b %Y, %H:%M:%S")

    def _sym(self, pair: str) -> str:
        return pair.upper().replace("_", "/")


    def _cmd_start(self, mid, chat_id=None):
        self._send(
            f"{E['rocket']} <b>Selamat datang di Indodax Trading Bot!</b>\n"
            f"<code>{DLINE}</code>\n\n"
            f"{E['robot']} Bot trading otomatis multi-strategi.\n\n"
            f"<b>Perintah tersedia:</b>\n"
            f"<code>{LINE}</code>\n"
            f"{E['chart']} /status    — Status bot & statistik\n"
            f"{E['coin']} /harga     — Harga semua pair\n"
            f"{E['key']} /saldo     — Saldo akun Indodax\n"
            f"{E['diamond']} /posisi    — Posisi terbuka\n"
            f"{E['bar']} /laporan   — Performa hari ini\n"
            f"{E['clock']} /history   — 5 trade terakhir\n"
            f"{E['pin']} /pair      — Pair yang dimonitor\n"
            f"{E['info']} /config    — Konfigurasi bot\n"
            f"{E['stop']} /pause     — Pause (tidak buka posisi baru)\n"
            f"{E['check']} /resume    — Lanjutkan dari pause\n"
            f"{E['rocket']} /help      — Bantuan lengkap\n"
            f"<code>{LINE}</code>\n"
            f"{E['clock']} <i>{self._now()}</i>",
            reply_id=mid, to_chat=chat_id,
        )

    def _cmd_help(self, mid, chat_id=None):
        self._send(
            f"{E['rocket']} <b>Selamat datang di Indodax Trading Bot!</b>\n"
            f"<code>{DLINE}</code>\n\n"
            f"{E['robot']} Bot trading otomatis multi-strategi.\n\n"
            f"<b>Perintah tersedia:</b>\n"
            f"<code>{LINE}</code>\n"
            f"{E['chart']} /status    — Status bot & statistik\n"
            f"{E['coin']} /harga     — Harga semua pair\n"
            f"{E['key']} /saldo     — Saldo akun Indodax\n"
            f"{E['diamond']} /posisi    — Posisi terbuka\n"
            f"{E['bar']} /laporan   — Performa hari ini\n"
            f"{E['clock']} /history   — 5 trade terakhir\n"
            f"{E['pin']} /pair      — Pair yang dimonitor\n"
            f"{E['info']} /config    — Konfigurasi bot\n"
            f"{E['stop']} /pause     — Pause (tidak buka posisi baru)\n"
            f"{E['check']} /resume    — Lanjutkan dari pause\n"
            f"{E['rocket']} /help      — Bantuan lengkap\n"
            f"<code>{LINE}</code>\n"
            f"{E['warn']} Hanya chat ID terdaftar yang bisa mengirim perintah.\n"
            f"{E['clock']} <i>{self._now()}</i>",
            reply_id=mid, to_chat=chat_id,
        )

    def _cmd_status(self, mid, chat_id=None):
        db        = Database()
        stats     = db.get_overall_stats() or {}
        is_paused = os.path.exists(self.PAUSE_FLAG)
        total     = int(stats.get("total_trades") or 0)
        winrate   = float(stats.get("winrate") or 0)
        net_pnl   = float(stats.get("net_pnl") or 0)

        risk      = self.bot.risk
        portfolio = risk.get_portfolio_summary()
        mode_e    = E['lock'] if self.bot.dry_run else E['thunder']
        mode_t    = "DRY RUN" if self.bot.dry_run else "LIVE TRADING"
        st_e      = E['stop'] if is_paused else E['check']
        st_t      = "PAUSE" if is_paused else "AKTIF"
        pnl_e     = E['up'] if net_pnl >= 0 else E['down']

        # Visualisasi slot portfolio
        slot_rows = []
        for i in range(portfolio["max_slots"]):
            if i < len(portfolio["open_pairs"]):
                pair_name = portfolio["open_pairs"][i].upper().replace("_", "/")
                pos       = risk.positions.get(portfolio["open_pairs"][i])
                idr_inv   = f"Rp {pos.idr_invested:,.0f}" if pos else ""
                slot_rows.append(f"  {E['fire']} <code>Slot {i+1}</code> [{pair_name}] {idr_inv}")
            else:
                slot_rows.append(
                    f"  {E['check']} <code>Slot {i+1}</code> "
                    f"[KOSONG] — Rp {portfolio['slot_size']:,.0f} siap"
                )

        self._send(
            f"{E['robot']} <b>STATUS BOT</b>\n"
            f"<code>{DLINE}</code>\n\n"
            f"{st_e} <b>Status</b>    ›  <code>{st_t}</code>\n"
            f"{mode_e} <b>Mode</b>      ›  <code>{mode_t}</code>\n"
            f"{E['clock']} <b>Interval</b>  ›  <code>{BOT_CONFIG.get('loop_interval')}s</code>\n\n"
            f"<b>Portfolio Slots</b> ({portfolio['slots_used']}/{portfolio['max_slots']} terisi)\n"
            f"<code>{LINE}</code>\n"
            f"Saldo awal : <code>Rp {portfolio['initial_balance']:,.0f}</code>\n"
            f"Per slot   : <code>Rp {portfolio['slot_size']:,.0f}</code>\n\n"
            + "\n".join(slot_rows) +
            f"\n\n<code>{LINE}</code>\n"
            f"Deployed   : <code>Rp {portfolio['idr_deployed']:,.0f}</code>\n"
            f"Tersedia   : <code>Rp {portfolio['idr_free_slots']:,.0f}</code>\n\n"
            f"<b>Statistik Keseluruhan:</b>\n"
            f"{E['chart']} Total Trade  ›  <code>{total}</code>\n"
            f"{E['target']} Win Rate    ›  <code>{winrate:.1f}%</code>\n"
            f"{pnl_e} Net PnL     ›  <code>Rp {net_pnl:+,.0f}</code>\n\n"
            f"{E['clock']} <i>{self._now()}</i>",
            reply_id=mid, to_chat=chat_id,
        )

    def _cmd_harga(self, mid, chat_id=None):
        self._send(f"{E['chart']} Mengambil harga real-time...", reply_id=mid, to_chat=chat_id)
        try:
            import requests as req
            r = req.get(
                "https://indodax.com/api/summaries",
                headers={"User-Agent": "Mozilla/5.0 Chrome/122.0.0.0"},
                timeout=10,
            )
            tickers_raw = {}
            if r.status_code == 200:
                data = r.json().get("tickers", {})
                for pair in TRADING_PAIRS:
                    key = pair.replace("_", "")
                    if key in data:
                        tickers_raw[pair] = data[key]

            if not tickers_raw:
                tickers_raw = self.bot.api._ticker_cache or {}

            rows = []
            for pair in TRADING_PAIRS:
                t     = tickers_raw.get(pair, {})
                price = float(t.get("last", 0) or 0)
                high  = float(t.get("high", 0) or 0)
                low   = float(t.get("low",  0) or 0)
                sym   = self._sym(pair)
                if price == 0:
                    rows.append(f"  {E['cross']} <code>{sym:<10}</code>  —")
                else:
                    rows.append(
                        f"  {E['diamond']} <b>{sym:<10}</b> "
                        f"<code>Rp {price:>14,.0f}</code>\n"
                        f"     <code>H:{high:>12,.0f}  L:{low:>12,.0f}</code>"
                    )
            self._send(
                f"{E['chart']} <b>HARGA REAL-TIME</b>\n"
                f"<code>{DLINE}</code>\n\n"
                + "\n\n".join(rows) +
                f"\n\n<code>{LINE}</code>\n"
                f"{E['clock']} <i>{self._now()}</i>",
                to_chat=chat_id,
            )
        except Exception as e:
            self._send(f"{E['cross']} Gagal ambil harga: {e}", to_chat=chat_id)

    def _cmd_saldo(self, mid, chat_id=None):
        self._send(f"{E['key']} Mengambil saldo...", reply_id=mid, to_chat=chat_id)
        try:
            info = self.bot.api.get_balance()
            bal  = info.get("balance", {})
            idr  = float(bal.get("idr", 0) or 0)

            portfolio = self.bot.risk.get_portfolio_summary()
            slot_size = portfolio.get("slot_size", 0)
            slots_used = portfolio.get("slots_used", 0)
            max_slots  = portfolio.get("max_slots", 4)

            rows = [f"  {E['coin']} <b>IDR</b>    ›  <code>Rp {idr:,.0f}</code>"]
            for pair in TRADING_PAIRS:
                coin = pair.replace("_idr", "")
                amt  = float(bal.get(coin, 0) or 0)
                if amt > 0:
                    rows.append(
                        f"  {E['diamond']} <b>{coin.upper():<6}</b> ›  "
                        f"<code>{amt:.8f}</code>"
                    )

            self._send(
                f"{E['key']} <b>SALDO AKUN INDODAX</b>\n"
                f"<code>{DLINE}</code>\n\n"
                + "\n".join(rows) +
                f"\n\n<code>{LINE}</code>\n"
                f"<b>Portfolio Slot:</b>\n"
                f"  Per slot   : <code>Rp {slot_size:,.0f}</code>\n"
                f"  Slot penuh : <code>{slots_used}/{max_slots}</code>\n"
                f"  Saldo awal : <code>Rp {portfolio.get('initial_balance',0):,.0f}</code>\n\n"
                f"{E['clock']} <i>{self._now()}</i>",
                to_chat=chat_id,
            )
        except Exception as e:
            self._send(
                f"{E['cross']} Gagal ambil saldo: <code>{e}</code>\n"
                f"<i>Pastikan API Key Indodax masih valid.</i>",
                to_chat=chat_id,
            )
        except Exception as e:
            self._send(f"{E['cross']} Gagal ambil saldo: {e}")

    def _cmd_posisi(self, mid, chat_id=None):
        positions = self.bot.risk.positions
        if not positions:
            self._send(
                f"{E['diamond']} <b>POSISI TERBUKA</b>\n"
                f"<code>{LINE}</code>\n"
                f"<i>Tidak ada posisi terbuka saat ini.</i>\n"
                f"{E['clock']} <i>{self._now()}</i>",
                reply_id=mid,
            )
            return
        rows = []
        for pair, pos in positions.items():
            sym   = self._sym(pair)
            price = self.bot.api.get_current_price(pair) or pos.entry_price
            unreal_pct = (price - pos.entry_price) / pos.entry_price * 100
            unreal_idr = (price - pos.entry_price) * pos.coin_amount
            sign  = "+" if unreal_idr >= 0 else ""
            pnl_e = E['up'] if unreal_idr >= 0 else E['down']
            rows.append(
                f"{E['diamond']} <b>{sym}</b>\n"
                f"  {E['pin']} Entry    ›  <code>Rp {pos.entry_price:,.0f}</code>\n"
                f"  {E['chart']} Harga   ›  <code>Rp {price:,.0f}</code>\n"
                f"  {pnl_e} Unrealized ›  <code>{sign}{unreal_pct:.2f}% "
                f"(Rp {sign}{unreal_idr:,.0f})</code>\n"
                f"  {E['warn']} SL      ›  <code>Rp {pos.stop_loss:,.0f}</code>\n"
                f"  {E['profit']} TP     ›  <code>Rp {pos.take_profit:,.0f}</code>"
            )
        self._send(
            f"{E['eye']} <b>POSISI TERBUKA ({len(positions)})</b>\n"
            f"<code>{DLINE}</code>\n\n"
            + "\n\n".join(rows) +
            f"\n\n<code>{LINE}</code>\n"
            f"{E['clock']} <i>{self._now()}</i>",
            reply_id=mid, to_chat=chat_id,
        )

    def _cmd_laporan(self, mid, chat_id=None):
        from datetime import date
        db      = Database()
        trades  = db.get_trade_history(limit=500)
        today   = date.today().isoformat()
        today_t = [t for t in trades if str(t.get("created_at","")).startswith(today)]
        total   = len(today_t)
        wins    = sum(1 for t in today_t if float(t.get("pnl_idr") or 0) > 0)
        losses  = total - wins
        net     = sum(float(t.get("pnl_idr") or 0) for t in today_t)
        wr      = (wins / total * 100) if total else 0
        bar     = "█" * int(wr / 10) + "░" * (10 - int(wr / 10))
        sign    = "+" if net >= 0 else ""
        pnl_e   = E['profit'] if net >= 0 else E['loss']
        self._send(
            f"{E['bar']} <b>LAPORAN HARI INI</b>\n"
            f"<code>{DLINE}</code>\n\n"
            f"{E['sun']} <b>{datetime.now().strftime('%d %B %Y')}</b>\n\n"
            f"{E['chart']} Total Trade  ›  <code>{total}</code>\n"
            f"{E['check']} Menang      ›  <code>{wins}</code>\n"
            f"{E['cross']} Kalah       ›  <code>{losses}</code>\n\n"
            f"{E['target']} <b>Win Rate</b>\n"
            f"   <code>[{bar}] {wr:.1f}%</code>\n\n"
            f"<code>{LINE}</code>\n"
            f"{pnl_e} <b>Net PnL</b>    ›  <code>Rp {sign}{net:,.0f}</code>\n\n"
            f"{E['clock']} <i>{self._now()}</i>",
            reply_id=mid, to_chat=chat_id,
        )

    def _cmd_history(self, mid, chat_id=None):
        db     = Database()
        trades = db.get_trade_history(limit=5)
        if not trades:
            self._send(
                f"{E['clock']} Belum ada riwayat trade.\n<i>{self._now()}</i>",
                reply_id=mid,
            )
            return
        rows = []
        for t in trades:
            pnl  = float(t.get("pnl_idr") or 0)
            pct  = float(t.get("pnl_pct") or 0)
            sym  = self._sym(t.get("pair",""))
            sign = "+" if pnl >= 0 else ""
            e    = E['up'] if pnl >= 0 else E['down']
            ep   = float(t.get("entry_price") or 0)
            xp   = float(t.get("exit_price") or 0)
            ts   = str(t.get("created_at",""))[:16]
            rows.append(
                f"{e} <b>{sym}</b>  <code>{sign}Rp {pnl:,.0f} ({sign}{pct:.1f}%)</code>\n"
                f"  <code>{ep:,.0f} -> {xp:,.0f}</code>  <i>{ts}</i>"
            )
        self._send(
            f"{E['clock']} <b>5 TRADE TERAKHIR</b>\n"
            f"<code>{DLINE}</code>\n\n"
            + "\n\n".join(rows) +
            f"\n\n<code>{LINE}</code>\n"
            f"{E['clock']} <i>{self._now()}</i>",
            reply_id=mid, to_chat=chat_id,
        )

    def _cmd_pair(self, mid, chat_id=None):
        pairs_str = "\n".join(
            f"  {i+1}. {E['diamond']} <code>{self._sym(p)}</code>"
            for i, p in enumerate(TRADING_PAIRS)
        )
        self._send(
            f"{E['pin']} <b>PAIR AKTIF ({len(TRADING_PAIRS)})</b>\n"
            f"<code>{DLINE}</code>\n\n"
            f"{pairs_str}\n\n"
            f"{E['clock']} <i>{self._now()}</i>",
            reply_id=mid, to_chat=chat_id,
        )

    def _cmd_config(self, mid, chat_id=None):
        bc = BOT_CONFIG
        rc = RISK_CONFIG
        self._send(
            f"{E['info']} <b>KONFIGURASI BOT</b>\n"
            f"<code>{DLINE}</code>\n\n"
            f"<b>Bot Settings</b>\n"
            f"{E['clock']} Interval     ›  <code>{bc.get('loop_interval')}s</code>\n"
            f"{E['chart']} Timeframe    ›  <code>{bc.get('timeframe')}</code>\n"
            f"{E['bar']} Candle limit  ›  <code>{bc.get('candle_limit')}</code>\n"
            f"{E['thunder']} Mode         ›  <code>{'DRY RUN' if bc.get('dry_run') else 'LIVE'}</code>\n\n"
            f"<code>{LINE}</code>\n"
            f"<b>Risk Management</b>\n"
            f"{E['warn']} Stop Loss     ›  <code>{rc.get('stop_loss_pct',0)*100:.1f}%</code>\n"
            f"{E['target']} Take Profit  ›  <code>{rc.get('take_profit_pct',0)*100:.1f}%</code>\n"
            f"{E['shield']} Trailing     ›  <code>{rc.get('trailing_stop_pct',0)*100:.1f}%</code>\n"
            f"{E['coin']} Trade Size    ›  <code>{rc.get('trade_size_pct',0)*100:.0f}% per trade</code>\n"
            f"{E['down']} Max Loss/hari ›  <code>Rp {rc.get('max_daily_loss_idr',0):,.0f}</code>\n"
            f"{E['chart']} Max Trade/hari ›  <code>{rc.get('max_trades_per_day')}</code>\n\n"
            f"{E['clock']} <i>{self._now()}</i>",
            reply_id=mid, to_chat=chat_id,
        )

    def _cmd_pause(self, mid, chat_id=None):
        os.makedirs("data", exist_ok=True)
        with open(self.PAUSE_FLAG, "w") as f:
            f.write(self._now())
        self._send(
            f"{E['stop']} <b>BOT DIPAUSE</b>\n"
            f"<code>{LINE}</code>\n"
            f"Bot tidak akan membuka posisi baru.\n"
            f"Posisi terbuka tetap dimonitor.\n\n"
            f"Ketik /resume untuk melanjutkan.\n"
            f"{E['clock']} <i>{self._now()}</i>",
            reply_id=mid, to_chat=chat_id,
        )
        logger.info("[CMD] Bot di-pause via Telegram")

    def _cmd_resume(self, mid, chat_id=None):
        if os.path.exists(self.PAUSE_FLAG):
            os.remove(self.PAUSE_FLAG)
            msg = (
                f"{E['check']} <b>BOT DILANJUTKAN</b>\n"
                f"<code>{LINE}</code>\n"
                f"Bot kembali aktif membuka posisi baru.\n"
                f"{E['clock']} <i>{self._now()}</i>"
            )
        else:
            msg = (
                f"{E['info']} Bot tidak dalam kondisi pause.\n"
                f"{E['clock']} <i>{self._now()}</i>"
            )
        self._send(msg, reply_id=mid, to_chat=chat_id)
        logger.info("[CMD] Bot di-resume via Telegram")

    def _cmd_unknown(self, mid, cmd):
        self._send(
            f"{E['warn']} Perintah <code>{cmd}</code> tidak dikenal.\n"
            f"Ketik /help untuk daftar perintah.",
            reply_id=mid, to_chat=chat_id,
        )


    HANDLERS = {
        "/start":   "_cmd_start",
        "/help":    "_cmd_help",
        "/status":  "_cmd_status",
        "/harga":   "_cmd_harga",
        "/saldo":   "_cmd_saldo",
        "/posisi":  "_cmd_posisi",
        "/laporan": "_cmd_laporan",
        "/history": "_cmd_history",
        "/pair":    "_cmd_pair",
        "/config":  "_cmd_config",
        "/pause":   "_cmd_pause",
        "/resume":  "_cmd_resume",
    }

    def _handle_update(self, update: dict):
        msg     = update.get("message", {})
        if not msg:
            return
        chat_id = str(msg.get("chat", {}).get("id", "")).strip()
        msg_id  = msg.get("message_id")
        text    = (msg.get("text") or "").strip()

        if chat_id not in self.allowed_ids:
            self.log.warning(
                f"[CMD] Ditolak dari chat {chat_id} "
                f"(allowed: {self.allowed_ids})"
            )
            return
        if not text.startswith("/"):
            return

        cmd = text.split()[0].split("@")[0].lower()
        self.log.info(f"[CMD] {cmd} dari {chat_id}")

        handler_name = self.HANDLERS.get(cmd)
        if handler_name:
            try:
                getattr(self, handler_name)(msg_id, chat_id)
            except TypeError:
                getattr(self, handler_name)(msg_id)
            except Exception as e:
                self.log.exception(f"[CMD] Error handler {cmd}: {e}")
                self._send(f"{E['cross']} Error: {str(e)[:100]}", reply_id=msg_id, to_chat=chat_id)
        else:
            self._cmd_unknown(msg_id, cmd, chat_id)


    def run(self):
        self.log.info("[CMD] Telegram command handler thread dimulai")
        self._send(
            f"{E['check']} <b>Bot + Command Handler aktif!</b>\n"
            f"Ketik /help untuk daftar perintah.\n"
            f"{E['clock']} <i>{self._now()}</i>"
        )

        offset      = None
        poll_timeout = 30
        http_timeout = poll_timeout + 10
        retry_count  = 0

        while self.running:
            try:
                params = {
                    "timeout":         poll_timeout,
                    "allowed_updates": ["message"],
                }
                if offset:
                    params["offset"] = offset

                data = self._get("getUpdates", params, timeout=http_timeout)

                if not data.get("ok"):
                    if data:
                        self.log.warning(f"[CMD] getUpdates: {data.get('description','')}")
                        time.sleep(5)
                    else:
                        retry_count += 1
                        time.sleep(min(retry_count * 2, 30))
                    continue

                retry_count = 0
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    self._handle_update(upd)

            except Exception as e:
                self.log.warning(f"[CMD] Loop error: {e}")
                time.sleep(3)

        self.log.info("[CMD] Command handler thread berhenti")

    def stop(self):
        self.running = False


class TradingBot:

    def __init__(self, dry_run: bool = None, start_cmd_thread: bool = True):
        self.api      = IndodaxAPI()
        self.strategy = StrategyEngine()
        self.risk     = RiskManager()
        self.db       = Database()
        self.notifier = TelegramNotifier()
        self.running  = False
        self.dry_run  = dry_run if dry_run is not None else BOT_CONFIG.get("dry_run", False)

        self.notifier.set_dry_run(self.dry_run)

        self._startup_ohlcv_check()

        self.cmd_thread = None
        if start_cmd_thread and self.notifier.enabled:
            self.cmd_thread = TelegramCommandThread(bot_ref=self)
            self.cmd_thread.start()
            logger.info("[BOT] Telegram command handler thread dimulai")

        logger.info("=" * 60)
        logger.info("[BOT] Indodax Trading Bot Initialized")
        logger.info(f"   Pairs    : {', '.join(TRADING_PAIRS)}")
        logger.info(f"   Interval : {BOT_CONFIG['loop_interval']}s")
        logger.info(f"   Dry Run  : {self.dry_run}")
        cmd_status = "aktif" if self.cmd_thread else "nonaktif (Telegram disabled)"
        logger.info(f"   Telegram : {cmd_status}")
        logger.info("=" * 60)

    def _startup_ohlcv_check(self):
        test_pair = TRADING_PAIRS[0] if TRADING_PAIRS else "btc_idr"
        logger.info(f"[BOT] Startup OHLCV check: {test_pair}...")
        try:
            candles = self.api.get_ohlcv(
                test_pair,
                tf=BOT_CONFIG["timeframe"].replace("m", ""),
                limit=10,
            )
            if candles and len(candles) >= 5:
                closes = [c.get("close", 0) for c in candles]
                unique = len(set(round(c, -2) for c in closes))
                if unique > 2:
                    logger.info(
                        f"[BOT] OHLCV OK - {len(candles)} candles real "
                        f"({unique} harga berbeda)"
                    )
                else:
                    logger.warning(
                        f"[BOT] OHLCV menggunakan data sintetis untuk {test_pair}. "
                        f"ccxt mungkin terblokir di environment ini. "
                        f"Strategi tetap jalan tapi akurasi sinyal berkurang."
                    )
            else:
                logger.warning(f"[BOT] OHLCV kosong untuk {test_pair} saat startup")
        except Exception as e:
            logger.warning(f"[BOT] Startup OHLCV check error: {e}")


    def _setup_signals(self):
        def handler(sig, frame):
            logger.info("\n[BOT] Shutdown signal diterima...")
            self.running = False
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)


    def run(self) -> None:

        self._setup_signals()
        self.running = True

        logger.info("[BOT] Mengambil saldo awal untuk portfolio slot...")
        try:
            real_balance = self.api.get_available_idr() if not self.dry_run else 100_000
            if real_balance <= 0:
                real_balance = RISK_CONFIG.get("initial_balance_idr", 0) or 100_000
                logger.warning(f"[BOT] Saldo 0, gunakan default Rp {real_balance:,.0f}")
            self.risk.set_initial_balance(real_balance)
        except Exception as e:
            logger.warning(f"[BOT] Gagal ambil saldo, pakai default: {e}")
            self.risk.set_initial_balance(
                RISK_CONFIG.get("initial_balance_idr", 0) or 100_000
            )

        self.notifier.notify_bot_start(TRADING_PAIRS)
        logger.info("[BOT] Bot mulai berjalan...")

        iteration = 0
        while self.running:
            iteration += 1
            start_time = time.time()
            logger.info(f"\n{'-'*50}")
            logger.info(f"[BOT] Iterasi #{iteration} | {datetime.now().strftime('%H:%M:%S')}")

            # Cek status risk harian
            risk_status = self.risk.get_status()
            if risk_status["halted"]:
                logger.warning(f"[BOT] HALTED: {risk_status['halt_reason']}")
                time.sleep(60)
                continue

            # Cek pause flag dari Telegram /pause command
            if os.path.exists(TelegramCommandThread.PAUSE_FLAG):
                logger.info("[BOT] PAUSE aktif (via Telegram /pause) - skip iterasi ini")
                time.sleep(30)
                continue

            self.api.clear_ohlcv_cache()
            ticker_ok = False
            try:
                result = self.api.fetch_all_tickers(TRADING_PAIRS)
                ticker_ok = bool(result)
                if not ticker_ok:
                    backoff = BOT_CONFIG.get("rate_limit_backoff", 30)
                    logger.warning(
                        f"[BOT] Semua ticker gagal (rate limit?), "
                        f"tunggu {backoff}s sebelum lanjut..."
                    )
                    time.sleep(backoff)
                    continue
            except Exception as e:
                logger.warning(f"[BOT] Batch ticker error: {e}")
                time.sleep(BOT_CONFIG.get("rate_limit_backoff", 30))
                continue

            pair_delay = BOT_CONFIG.get("pair_delay", 0.5)
            for i, pair in enumerate(TRADING_PAIRS):
                if not self.running:
                    break
                try:
                    self._process_pair(pair)
                except IndodaxAPIError as e:
                    logger.error(f"[BOT] API error untuk {pair}: {e}")
                    self.db.log_error("ERROR", str(e), pair)
                except Exception as e:
                    logger.exception(f"[BOT] Unexpected error untuk {pair}: {e}")
                    self.notifier.notify_error(str(e), pair)
  
                if i < len(TRADING_PAIRS) - 1 and pair_delay > 0:
                    time.sleep(pair_delay)

            elapsed  = time.time() - start_time
            sleep_t  = max(0, BOT_CONFIG["loop_interval"] - elapsed)
            logger.info(f"[BOT] Selesai dalam {elapsed:.1f}s | Tidur {sleep_t:.1f}s")
            time.sleep(sleep_t)

        self._graceful_shutdown()

    def run_once(self) -> None:
        logger.info("[BOT] run_once - fetch batch ticker...")
        try:
            self.api.fetch_all_tickers(TRADING_PAIRS)
        except Exception as e:
            logger.warning(f"[BOT] Batch ticker error: {e}")
        for pair in TRADING_PAIRS:
            try:
                self._process_pair(pair)
            except Exception as e:
                logger.error(f"[BOT] Error {pair}: {e}")


    def _process_pair(self, pair: str) -> None:

        logger.info(f"[BOT] Proses {pair.upper()}")

        # ── 1. Ambil harga terkini ──
        current_price = self.api.get_current_price(pair)
        if not current_price:
            logger.warning(f"[BOT] Gagal ambil harga {pair}")
            return

        if pair in self.risk.positions:
            should_exit, exit_reason = self.risk.check_exit_signals(pair, current_price)
            if should_exit:
                logger.info(f"[BOT] EXIT {pair}: {exit_reason}")
                self._execute_sell(pair, current_price, exit_reason)
                return
            else:
                pos = self.risk.positions[pair]
                unreal_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                sign = "+" if unreal_pct >= 0 else ""
                logger.info(
                    f"[BOT] HOLD {pair} | "
                    f"entry={pos.entry_price:,.0f} now={current_price:,.0f} | "
                    f"PnL={sign}{unreal_pct:.2f}% | "
                    f"SL={pos.stop_loss:,.0f} TP={pos.take_profit:,.0f}"
                )

        candles = self.api.get_ohlcv(
            pair,
            tf=BOT_CONFIG["timeframe"].replace("m", ""),
            limit=BOT_CONFIG["candle_limit"],
        )

        if not candles:
            logger.warning(f"[BOT] Tidak ada data candle untuk {pair}")
            return

        closes = [c.get("close", 0) for c in candles[-10:]]
        is_synthetic = len(set(round(c, -2) for c in closes)) <= 2 and len(candles) >= 10
        if is_synthetic:
            logger.debug(f"[BOT] {pair} candle sintetis, skip analisis entry")
            return

        depth = self.api.get_depth(pair)

        decision = self.strategy.analyze_and_decide(candles, depth if depth else None)
        summary  = self.strategy.get_signal_summary(decision)
        logger.info(f"[BOT] {pair} -> {summary}")

        ind = decision.indicator_result
        if ind:
            self.db.save_snapshot(pair, current_price, ind, decision)

        if pair in self.risk.positions:
            if decision.action == Signal.SELL:
                exit_reason = f"STRATEGY SELL: {', '.join(decision.reasons[:2])}"
                self._execute_sell(pair, current_price, exit_reason, decision)
            return  
        
        if decision.action != Signal.BUY:
            return

        if self.dry_run:
            available_idr = self.risk._initial_balance or 100_000
        else:
            available_idr = self.api.get_available_idr()

        allowed, deny_reason = self.risk.can_enter_trade(pair, available_idr)
        if not allowed:
            logger.info(f"[BOT] Trade ditolak: {deny_reason}")
            return

        self._execute_buy(pair, current_price, available_idr, decision)


    def _execute_buy(
        self,
        pair: str,
        price: float,
        available_idr: float,
        decision,
    ) -> None:
        trade_size  = self.risk.calc_trade_size(available_idr)
        if trade_size <= 0:
            logger.warning(f"[BUY] Trade size 0 untuk {pair}, skip")
            return
        coin_amount = trade_size / price

        portfolio     = self.risk.get_portfolio_summary()
        strategy_name = ", ".join(decision.strategies_agreed) or "Mixed"
        reason        = " | ".join(decision.reasons[:3])
        slots_after   = portfolio["slots_used"] + 1

        logger.info(
            f"[BUY] {pair} | price={price:,.0f} | "
            f"slot={trade_size:,.0f} IDR | "
            f"slot [{slots_after}/{self.risk._max_slots}] | "
            f"confidence={decision.confidence:.0%}"
        )

        order_id = ""
        if not self.dry_run:
            try:
                resp = self.api.place_buy_order(pair, price * 0.999, trade_size)
                order_id = str(resp.get("order_id", ""))
            except IndodaxAPIError as e:
                logger.error(f"[BUY] Order gagal: {e}")
                return

        # Catat posisi
        pos = self.risk.open_position(
            pair=pair,
            entry_price=price,
            coin_amount=coin_amount,
            idr_invested=trade_size,
            order_id=order_id,
        )

        self._save_positions()

        trade_record = {
            "pair":         pair,
            "trade_type":   "BUY",
            "entry_price":  price,
            "exit_price":   0,
            "coin_amount":  coin_amount,
            "idr_invested": trade_size,
            "idr_received": 0,
            "pnl_idr":      0,
            "pnl_pct":      0,
            "strategy":     strategy_name,
            "reason":       reason,
            "order_id":     order_id,
            "dry_run":      self.dry_run,
            "entry_time":   datetime.now(),
            "exit_time":    None,
        }
        self.db.save_trade(trade_record)

        ind       = decision.indicator_result
        rsi_val   = getattr(ind, "rsi", 0) if ind else 0
        trend_val = getattr(ind, "trend", "") if ind else ""
        portfolio = self.risk.get_portfolio_summary()
        slot_info = (
            f"Slot [{portfolio['slots_used']}/{portfolio['max_slots']}] | "
            f"Rp {trade_size:,.0f} dari saldo Rp {portfolio['initial_balance']:,.0f}"
        )

        self.notifier.notify_buy(
            pair=pair,
            price=price,
            amount_idr=trade_size,
            coin_amount=coin_amount,
            strategy=strategy_name,
            confidence=decision.confidence,
            reason=f"{slot_info}\n{reason}",
            dry_run=self.dry_run,
            rsi=rsi_val,
            trend=trend_val,
        )

    def _execute_sell(
        self,
        pair: str,
        price: float,
        reason: str,
        decision=None,
    ) -> None:
        pos = self.risk.positions.get(pair)
        if not pos:
            return

        logger.info(f"[SELL] {pair} | price={price:,.0f} | reason={reason}")

        order_id = ""
        if not self.dry_run:
            try:
                resp = self.api.place_sell_order(pair, price * 1.001, pos.coin_amount)
                order_id = str(resp.get("order_id", ""))
            except IndodaxAPIError as e:
                logger.error(f"[SELL] Order gagal: {e}")
                return

        entry_price_snap = pos.entry_price 

        pnl_data = self.risk.close_position(pair, price)
        if not pnl_data:
            return

        self._save_positions()

        trade_record = {
            "pair":         pair,
            "trade_type":   "SELL",
            "entry_price":  pnl_data["entry_price"],
            "exit_price":   price,
            "coin_amount":  pnl_data["coin_amount"],
            "idr_invested": pnl_data["idr_invested"],
            "idr_received": pnl_data["idr_received"],
            "pnl_idr":      pnl_data["pnl_idr"],
            "pnl_pct":      pnl_data["pnl_pct"],
            "strategy":     reason,
            "reason":       reason,
            "duration_min": pnl_data["duration_min"],
            "order_id":     order_id,
            "dry_run":      self.dry_run,
            "entry_time":   pnl_data["entry_time"],
            "exit_time":    pnl_data["exit_time"],
        }
        self.db.save_trade(trade_record)

        self.notifier.notify_sell(
            pair=pair,
            exit_price=price,
            pnl_idr=pnl_data["pnl_idr"],
            pnl_pct=pnl_data["pnl_pct"],
            reason=reason,
            duration_min=pnl_data["duration_min"],
            dry_run=self.dry_run,
            entry_price=entry_price_snap,
        )

    def _save_positions(self) -> None:
        import json
        try:
            os.makedirs("data", exist_ok=True)
            positions = {}
            for pair, pos in self.risk.positions.items():
                positions[pair] = {
                    "entry_price":   pos.entry_price,
                    "coin_amount":   pos.coin_amount,
                    "idr_invested":  pos.idr_invested,
                    "stop_loss":     pos.stop_loss,
                    "take_profit":   pos.take_profit,
                    "entry_time":    str(pos.entry_time),
                }
            with open("data/open_positions.json", "w") as f:
                json.dump(positions, f, indent=2)
        except Exception as e:
            logger.debug(f"[BOT] _save_positions error: {e}")


    def _graceful_shutdown(self) -> None:
        logger.info("[BOT] Graceful shutdown dimulai...")

        if self.cmd_thread and self.cmd_thread.is_alive():
            logger.info("[BOT] Menghentikan Telegram command thread...")
            self.cmd_thread.stop()

        risk_status = self.risk.get_status()
        logger.info(
            f"[BOT] Stats hari ini: "
            f"trades={risk_status['trades_today']} | "
            f"PnL={risk_status['daily_pnl']:+,.0f} IDR"
        )

        self.notifier.notify_bot_stop("User stopped bot")

        stats = self.db.get_overall_stats()
        if stats:
            total = int(stats.get("total_trades") or 0)
            wr    = float(stats.get("winrate") or 0)
            net   = float(stats.get("net_pnl") or 0)
            logger.info(
                f"[BOT] Total stats: trades={total} | WR={wr:.1f}% | Net={net:+,.0f} IDR"
            )

        logger.info("[BOT] Bot berhasil dihentikan")


def main():
    parser = argparse.ArgumentParser(
        description="Indodax Trading Bot",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="Dry run - analisis saja, TIDAK eksekusi order sungguhan",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Live trading - eksekusi order SUNGGUHAN (hati-hati!)",
    )
    parser.add_argument("--once",     action="store_true", help="Jalankan satu iterasi saja")
    parser.add_argument("--backtest", action="store_true", help="Jalankan backtesting")
    parser.add_argument("--pair",     default="",          help="Pair untuk backtest")
    parser.add_argument("--csv",      default="",          help="Path CSV historis untuk backtest")
    parser.add_argument(
        "--no-cmd", action="store_true",
        help="Nonaktifkan Telegram command handler (jalankan terpisah)"
    )
    args = parser.parse_args()

    if args.backtest:
        from backtest import Backtester
        bt   = Backtester()
        pair = args.pair or "btc_idr"
        if args.csv and os.path.exists(args.csv):
            df = bt.load_csv(args.csv)
        else:
            logger.info("[BACKTEST] Menggunakan data simulasi (gunakan --csv untuk data real)")
            df = bt.generate_sample_data(pair, n=1000)
        result = bt.run(df, pair=pair)
        bt.print_report(result)
        os.makedirs("data", exist_ok=True)
        bt.export_trades_csv(result, f"data/backtest_{pair}.csv")
        return

    if args.dry:
        dry_run = True
    elif args.live:
        dry_run = False
    else:
        dry_run = BOT_CONFIG.get("dry_run", False)

    if dry_run:
        print("\n" + "="*55)
        print("  MODE: DRY RUN (tidak ada order sungguhan)")
        print("  Gunakan 'python main.py --live' untuk live trading")
        print("="*55 + "\n")
    else:
        print("\n" + "="*55)
        print("  MODE: LIVE TRADING (order SUNGGUHAN akan dieksekusi!)")
        print("  Pastikan API key sudah diisi di .env")
        print("="*55 + "\n")

    bot = TradingBot(dry_run=dry_run, start_cmd_thread=not args.no_cmd)

    if args.once:
        bot.run_once()
    else:
        bot.run()


if __name__ == "__main__":
    main()
