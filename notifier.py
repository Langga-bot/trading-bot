import logging
import requests
from datetime import datetime
from typing import Optional

from config import TELEGRAM_CONFIG, BOT_CONFIG, RISK_CONFIG

logger = logging.getLogger(__name__)

# Emoji
E = {
    "buy":      "\U0001f7e2",   # 🟢
    "sell":     "\U0001f534",   # 🔴
    "profit":   "\U0001f4b0",   # 💰
    "loss":     "\U0001f4b8",   # 💸
    "fire":     "\U0001f525",   # 🔥
    "rocket":   "\U0001f680",   # 🚀
    "stop":     "\U0001f6d1",   # 🛑
    "warn":     "\u26a0\ufe0f", # ⚠️
    "alert":    "\U0001f6a8",   # 🚨
    "chart":    "\U0001f4ca",   # 📊
    "up":       "\U0001f4c8",   # 📈
    "down":     "\U0001f4c9",   # 📉
    "clock":    "\U0001f55b",   # 🕛
    "robot":    "\U0001f916",   # 🤖
    "check":    "\u2705",       # ✅
    "cross":    "\u274c",       # ❌
    "diamond":  "\U0001f48e",   # 💎
    "coin":     "\U0001fa99",   # 🪙
    "pin":      "\U0001f4cc",   # 📌
    "target":   "\U0001f3af",   # 🎯
    "info":     "\u2139\ufe0f", # ℹ️
    "shield":   "\U0001f6e1\ufe0f", # 🛡️
    "trophy":   "\U0001f3c6",   # 🏆
    "bar":      "\U0001f4ca",   # 📊
    "sun":      "\U0001f31e",   # 🌞
    "moon":     "\U0001f319",   # 🌙
    "eye":      "\U0001f441\ufe0f", # 👁️
    "key":      "\U0001f511",   # 🔑
    "spark":    "\u2728",       # ✨
    "thunder":  "\u26a1",       # ⚡
    "lock":     "\U0001f512",   # 🔒
}

LINE  = "\u2500" * 22           # ──────────────────────
DLINE = "\u2550" * 22           # ══════════════════════


class TelegramNotifier:

    def __init__(self):
        self.token   = TELEGRAM_CONFIG.get("bot_token", "").strip()
        self.chat_id = str(TELEGRAM_CONFIG.get("chat_id", "")).strip().strip('"').strip("'")
        self.dry_run = BOT_CONFIG.get("dry_run", False)

        PLACEHOLDER_TOKENS = {
            "", "YOUR_BOT_TOKEN", "your_bot_token_here",
            "1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ",
        }
        token_ok = (
            bool(self.token)
            and self.token not in PLACEHOLDER_TOKENS
            and ":" in self.token
            and len(self.token) > 25
            and self.token.split(":")[0].strip().isdigit()
            and len(self.token.split(":")[1].strip()) >= 30
        )
        PLACEHOLDER_CHATS = {"", "123456789", "YOUR_CHAT_ID"}
        chat_ok = (
            bool(self.chat_id)
            and self.chat_id not in PLACEHOLDER_CHATS
        )

        cfg_on = TELEGRAM_CONFIG.get("enabled", False)

        if not cfg_on:
            self.enabled = False
            logger.info("[NOTIF] Telegram nonaktif (enabled=False di config.py)")
        elif not token_ok:
            self.enabled = False
            logger.warning("[NOTIF] Telegram dinonaktifkan - Token tidak valid")
        elif not chat_ok:
            self.enabled = False
            logger.warning("[NOTIF] Telegram dinonaktifkan - Chat ID belum diisi")
        else:
            self.enabled = True
            logger.info(f"[NOTIF] Telegram aktif -> chat_id={self.chat_id}")

        self.base_url = f"https://api.telegram.org/bot{self.token}"


    def send(self, message: str, parse_mode: str = "HTML",
             disable_web_preview: bool = True) -> bool:
        if not self.enabled:
            return False

        payload = {
            "chat_id":                  self.chat_id,
            "text":                     message,
            "parse_mode":               parse_mode,
            "disable_web_page_preview": disable_web_preview,
        }

        for attempt in range(2):
            try:
                r = requests.post(
                    f"{self.base_url}/sendMessage",
                    json=payload,
                    timeout=20,      
                )
                data = r.json()
                if r.status_code == 200 and data.get("ok"):
                    return True
                code = data.get("error_code", r.status_code)
                desc = data.get("description", "Unknown")
                logger.warning(f"[NOTIF] Telegram gagal [{code}]: {desc}")
                if code in (401, 404):
                    self.enabled = False
                    return False
                return False
            except requests.exceptions.ReadTimeout:
                if attempt == 0:
                    logger.debug("[NOTIF] Telegram timeout, retry...")
                    continue
                logger.warning("[NOTIF] Telegram timeout 2x - skip")
                return False
            except requests.exceptions.ConnectionError:
                logger.warning("[NOTIF] Telegram koneksi gagal - skip")
                return False
            except Exception as e:
                logger.warning(f"[NOTIF] Telegram error: {e}")
                return False
        return False

    def send_reply(self, message: str, reply_to_id: int, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            return False
        try:
            r = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id":             self.chat_id,
                    "text":                message,
                    "parse_mode":          parse_mode,
                    "reply_to_message_id": reply_to_id,
                },
                timeout=10,
            )
            return r.status_code == 200 and r.json().get("ok", False)
        except Exception:
            return False


    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%d %b %Y, %H:%M:%S WIB")

    @staticmethod
    def _pair_display(pair: str) -> str:
        parts = pair.upper().split("_")
        return f"{parts[0]}/{parts[1]}" if len(parts) == 2 else pair.upper()

    @staticmethod
    def _pnl_bar(pct: float, width: int = 10) -> str:
        filled = min(int(abs(pct) / 5 * width), width)
        if pct >= 0:
            return "▰" * filled + "▱" * (width - filled)
        else:
            return "▱" * (width - filled) + "▰" * filled

    @staticmethod
    def _rsi_indicator(rsi: float) -> str:
        if rsi <= 30:   return f"\U0001f7e2 {rsi:.1f} (Oversold)"    # 🟢
        if rsi >= 70:   return f"\U0001f534 {rsi:.1f} (Overbought)"  # 🔴
        return f"\U0001f7e1 {rsi:.1f} (Normal)"                       # 🟡


    def notify_buy(
        self,
        pair: str,
        price: float,
        amount_idr: float,
        coin_amount: float,
        strategy: str,
        confidence: float,
        reason: str = "",
        dry_run: bool = None,
        rsi: float = 0,
        trend: str = "",
    ) -> None:
        is_dry  = dry_run if dry_run is not None else self.dry_run
        mode    = f"{E['lock']} <b>DRY RUN</b> | " if is_dry else ""
        sym     = self._pair_display(pair)
        conf_bar = "█" * int(confidence * 10) + "░" * (10 - int(confidence * 10))

        msg = (
            f"{E['rocket']} {mode}<b>SINYAL BUY TERDETEKSI</b>\n"
            f"<code>{DLINE}</code>\n"
            f"\n"
            f"{E['diamond']} <b>Pair</b>    ›  <code>{sym}</code>\n"
            f"{E['coin']} <b>Harga</b>   ›  <code>Rp {price:,.0f}</code>\n"
            f"{E['key']} <b>Modal</b>   ›  <code>Rp {amount_idr:,.0f}</code>\n"
            f"{E['chart']} <b>Jumlah</b>  ›  <code>{coin_amount:.8f} {sym.split('/')[0]}</code>\n"
            f"\n"
            f"<code>{LINE}</code>\n"
            f"{E['target']} <b>Strategi</b>  ›  {strategy}\n"
            f"{E['fire']} <b>Confidence</b>\n"
            f"     <code>[{conf_bar}] {confidence:.0%}</code>\n"
        )
        if rsi:
            msg += f"{E['up']} <b>RSI</b>      ›  {self._rsi_indicator(rsi)}\n"
        if trend:
            trend_emoji = E['up'] if "up" in trend else (E['down'] if "down" in trend else E['chart'])
            msg += f"{trend_emoji} <b>Trend</b>    ›  <code>{trend.capitalize()}</code>\n"
        msg += (
            f"\n"
            f"<code>{LINE}</code>\n"
            f"{E['clock']} <i>{self._now()}</i>\n"
            f"{E['spark']} <i>{reason[:100]}</i>"
        )
        if self.send(msg):
            logger.info(f"[NOTIF] BUY notif -> {sym} @ Rp {price:,.0f}")

    def notify_sell(
        self,
        pair: str,
        exit_price: float,
        pnl_idr: float,
        pnl_pct: float,
        reason: str = "",
        duration_min: int = 0,
        dry_run: bool = None,
        entry_price: float = 0,
    ) -> None:
        is_dry   = dry_run if dry_run is not None else self.dry_run
        mode     = f"{E['lock']} <b>DRY RUN</b> | " if is_dry else ""
        sym      = self._pair_display(pair)
        is_profit = pnl_idr >= 0
        status_e  = E["profit"] if is_profit else E["loss"]
        status_t  = "PROFIT" if is_profit else "LOSS"
        pnl_sign  = "+" if is_profit else ""
        bar       = self._pnl_bar(pnl_pct)

        msg = (
            f"{status_e} {mode}<b>ORDER SELL — {status_t}</b>\n"
            f"<code>{DLINE}</code>\n"
            f"\n"
            f"{E['diamond']} <b>Pair</b>      ›  <code>{sym}</code>\n"
        )
        if entry_price:
            msg += f"{E['pin']} <b>Entry</b>     ›  <code>Rp {entry_price:,.0f}</code>\n"
        msg += (
            f"{E['pin']} <b>Exit</b>      ›  <code>Rp {exit_price:,.0f}</code>\n"
            f"\n"
            f"<code>{LINE}</code>\n"
            f"{'📈' if is_profit else '📉'} <b>PnL IDR</b>  ›  "
            f"<code>Rp {pnl_sign}{pnl_idr:,.0f}</code>\n"
            f"{'📈' if is_profit else '📉'} <b>PnL %</b>    ›  "
            f"<code>{pnl_sign}{pnl_pct:.2f}%</code>\n"
            f"     <code>[{bar}]</code>\n"
            f"\n"
            f"{E['clock']} <b>Durasi</b>    ›  <code>{duration_min} menit</code>\n"
            f"{E['info']} <b>Alasan</b>    ›  {reason[:80]}\n"
            f"\n"
            f"<code>{LINE}</code>\n"
            f"{E['clock']} <i>{self._now()}</i>"
        )
        if self.send(msg):
            logger.info(f"[NOTIF] SELL notif -> {sym} PnL={pnl_sign}{pnl_idr:,.0f}")

    def notify_stop_loss(self, pair: str, price: float, loss_idr: float,
                         loss_pct: float, duration_min: int = 0) -> None:
        sym = self._pair_display(pair)
        msg = (
            f"{E['warn']} <b>STOP LOSS TRIGGERED</b>\n"
            f"<code>{DLINE}</code>\n"
            f"\n"
            f"{E['diamond']} <b>Pair</b>     ›  <code>{sym}</code>\n"
            f"{E['pin']} <b>Harga</b>    ›  <code>Rp {price:,.0f}</code>\n"
            f"{E['down']} <b>Loss IDR</b> ›  <code>Rp {loss_idr:,.0f}</code>\n"
            f"{E['down']} <b>Loss %</b>   ›  <code>{loss_pct:.2f}%</code>\n"
            f"{E['clock']} <b>Durasi</b>   ›  <code>{duration_min} menit</code>\n"
            f"\n"
            f"<code>{LINE}</code>\n"
            f"<i>Stop loss otomatis dieksekusi untuk membatasi kerugian.</i>\n"
            f"{E['clock']} <i>{self._now()}</i>"
        )
        self.send(msg)

    def notify_take_profit(self, pair: str, price: float, profit_idr: float,
                           profit_pct: float, duration_min: int = 0) -> None:
        sym = self._pair_display(pair)
        msg = (
            f"{E['trophy']} <b>TAKE PROFIT REACHED!</b>\n"
            f"<code>{DLINE}</code>\n"
            f"\n"
            f"{E['diamond']} <b>Pair</b>       ›  <code>{sym}</code>\n"
            f"{E['pin']} <b>Harga</b>      ›  <code>Rp {price:,.0f}</code>\n"
            f"{E['profit']} <b>Profit IDR</b> ›  <code>Rp +{profit_idr:,.0f}</code>\n"
            f"{E['up']} <b>Profit %</b>   ›  <code>+{profit_pct:.2f}%</code>\n"
            f"{E['clock']} <b>Durasi</b>    ›  <code>{duration_min} menit</code>\n"
            f"\n"
            f"<code>{LINE}</code>\n"
            f"<i>Target profit tercapai.</i>\n"
            f"{E['clock']} <i>{self._now()}</i>"
        )
        self.send(msg)

    def notify_circuit_breaker(self, loss_idr: float, max_loss: float) -> None:
        msg = (
            f"{E['alert']} <b>CIRCUIT BREAKER AKTIF!</b>\n"
            f"<code>{DLINE}</code>\n"
            f"\n"
            f"<b>Bot berhenti trading hari ini.</b>\n"
            f"\n"
            f"{E['down']} <b>Loss harian</b> ›  <code>Rp {loss_idr:,.0f}</code>\n"
            f"{E['shield']} <b>Batas loss</b>   ›  <code>Rp {max_loss:,.0f}</code>\n"
            f"\n"
            f"<code>{LINE}</code>\n"
            f"<i>Bot akan aktif kembali besok pukul 00:00.</i>\n"
            f"{E['clock']} <i>{self._now()}</i>"
        )
        self.send(msg)

    def notify_error(self, error_msg: str, context: str = "") -> None:
        msg = (
            f"{E['warn']} <b>BOT ERROR</b>\n"
            f"<code>{LINE}</code>\n"
            f"{E['cross']} <code>{error_msg[:200]}</code>\n"
        )
        if context:
            msg += f"{E['pin']} Context: <code>{context[:80]}</code>\n"
        msg += f"{E['clock']} <i>{self._now()}</i>"
        self.send(msg)

    def notify_daily_summary(
        self,
        trades: int,
        winning: int,
        losing: int,
        winrate: float,
        net_pnl: float,
        best_trade: float = 0,
        worst_trade: float = 0,
        halted: bool = False,
    ) -> None:
        status_e = E['stop'] if halted else E['check']
        status_t = "DIHENTIKAN" if halted else "AKTIF"
        pnl_e    = E['profit'] if net_pnl >= 0 else E['loss']
        sign     = "+" if net_pnl >= 0 else ""

        win_bar  = "█" * int(winrate / 10) + "░" * (10 - int(winrate / 10))

        msg = (
            f"{E['bar']} <b>LAPORAN HARIAN</b>\n"
            f"<code>{DLINE}</code>\n"
            f"\n"
            f"{E['sun']} <b>Tanggal</b>     ›  <code>{datetime.now().strftime('%d %B %Y')}</code>\n"
            f"\n"
            f"<b>Ringkasan Trading</b>\n"
            f"{E['chart']} Total Trade   ›  <code>{trades}</code>\n"
            f"{E['check']} Menang       ›  <code>{winning}</code>\n"
            f"{E['cross']} Kalah        ›  <code>{losing}</code>\n"
            f"\n"
            f"{E['target']} <b>Win Rate</b>\n"
            f"   <code>[{win_bar}] {winrate:.1f}%</code>\n"
            f"\n"
            f"<code>{LINE}</code>\n"
            f"{pnl_e} <b>Net PnL</b>     ›  <code>Rp {sign}{net_pnl:,.0f}</code>\n"
        )
        if best_trade:
            msg += f"{E['up']} <b>Best Trade</b>  ›  <code>Rp +{best_trade:,.0f}</code>\n"
        if worst_trade:
            msg += f"{E['down']} <b>Worst Trade</b> ›  <code>Rp {worst_trade:,.0f}</code>\n"
        msg += (
            f"\n"
            f"<code>{LINE}</code>\n"
            f"{status_e} <b>Status</b>  ›  {status_t}\n"
            f"{E['clock']} <i>{self._now()}</i>"
        )
        self.send(msg)

    def notify_bot_start(self, pairs: list) -> None:
        is_dry   = self.dry_run
        mode_e   = E['lock'] if is_dry else E['thunder']
        mode_t   = "DRY RUN" if is_dry else "LIVE TRADING"
        pairs_str = "\n".join(
            f"  {E['diamond']} <code>{self._pair_display(p)}</code>"
            for p in pairs
        )
        msg = (
            f"{E['rocket']} <b>BOT TRADING AKTIF</b>\n"
            f"<code>{DLINE}</code>\n"
            f"\n"
            f"{mode_e} <b>Mode</b>    ›  <code>{mode_t}</code>\n"
            f"{E['robot']} <b>Bot</b>     ›  Indodax Auto Trading\n"
            f"\n"
            f"{E['chart']} <b>Pair Aktif:</b>\n"
            f"{pairs_str}\n"
            f"\n"
            f"<code>{LINE}</code>\n"
            f"<i>Bot siap mendeteksi sinyal dan mengeksekusi order.</i>\n"
            f"{E['clock']} <i>{self._now()}</i>\n"
            f"\n"
            f"Ketik /status untuk cek kondisi bot."
        )
        self.send(msg)

    def notify_bot_stop(self, reason: str = "Manual stop") -> None:
        msg = (
            f"{E['stop']} <b>BOT TRADING BERHENTI</b>\n"
            f"<code>{DLINE}</code>\n"
            f"\n"
            f"{E['info']} <b>Alasan</b>  ›  {reason}\n"
            f"{E['clock']} <i>{self._now()}</i>\n"
            f"\n"
            f"<i>Jalankan kembali dengan:</i>\n"
            f"<code>python main.py --live</code>"
        )
        self.send(msg)

    def notify_signal_detected(
        self,
        pair: str,
        action: str,
        score: float,
        rsi: float,
        trend: str,
        reason: str = "",
    ) -> None:
        sym    = self._pair_display(pair)
        act_e  = E['buy'] if action == "BUY" else E['sell']
        msg = (
            f"{E['eye']} <b>SINYAL TERDETEKSI</b>\n"
            f"<code>{LINE}</code>\n"
            f"{act_e} <code>{action}</code> — <b>{sym}</b>\n"
            f"{E['target']} Score    ›  <code>{score:.1f}/10</code>\n"
            f"{E['up']} RSI      ›  {self._rsi_indicator(rsi)}\n"
            f"{E['chart']} Trend    ›  <code>{trend}</code>\n"
            f"{E['info']} <i>{reason[:100]}</i>\n"
            f"{E['clock']} <i>{self._now()}</i>"
        )
        self.send(msg)

    def notify_position_update(
        self,
        pair: str,
        entry_price: float,
        current_price: float,
        unrealized_pct: float,
        stop_loss: float,
        take_profit: float,
    ) -> None:
        sym    = self._pair_display(pair)
        is_pos = unrealized_pct >= 0
        pnl_e  = E['up'] if is_pos else E['down']
        sign   = "+" if is_pos else ""
        bar    = self._pnl_bar(unrealized_pct)
        msg = (
            f"{E['eye']} <b>UPDATE POSISI</b>\n"
            f"<code>{LINE}</code>\n"
            f"{E['diamond']} <b>{sym}</b>\n"
            f"\n"
            f"{E['pin']} Entry    ›  <code>Rp {entry_price:,.0f}</code>\n"
            f"{E['chart']} Harga   ›  <code>Rp {current_price:,.0f}</code>\n"
            f"{pnl_e} Unrealized ›  <code>{sign}{unrealized_pct:.2f}%</code>\n"
            f"   <code>[{bar}]</code>\n"
            f"\n"
            f"{E['warn']} SL   ›  <code>Rp {stop_loss:,.0f}</code>\n"
            f"{E['profit']} TP   ›  <code>Rp {take_profit:,.0f}</code>\n"
            f"{E['clock']} <i>{self._now()}</i>"
        )
        self.send(msg)

    def set_dry_run(self, dry_run: bool) -> None:
        self.dry_run = dry_run
