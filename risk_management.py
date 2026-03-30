import logging
from datetime import datetime, date
from typing import Optional, Dict
from dataclasses import dataclass, field

from config import RISK_CONFIG, BOT_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class Position:
    pair: str
    entry_price: float
    coin_amount: float
    idr_invested: float
    entry_time: datetime = field(default_factory=datetime.now)
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trailing_stop_price: float = 0.0
    highest_price: float = 0.0

    def __post_init__(self):
        if self.stop_loss == 0.0:
            self.stop_loss = self.entry_price * (1 - RISK_CONFIG["stop_loss_pct"])
        if self.take_profit == 0.0:
            self.take_profit = self.entry_price * (1 + RISK_CONFIG["take_profit_pct"])
        if self.trailing_stop_price == 0.0:
            self.trailing_stop_price = self.stop_loss
        self.highest_price = self.entry_price

    @property
    def current_pnl_pct(self, current_price: float = 0) -> float:
        if current_price and self.entry_price:
            return (current_price - self.entry_price) / self.entry_price
        return 0.0

    def update_trailing_stop(self, current_price: float) -> bool:
        trail_pct = RISK_CONFIG["trailing_stop_pct"]
        if current_price > self.highest_price:
            self.highest_price = current_price
            self.trailing_stop_price = current_price * (1 - trail_pct)
        return current_price <= self.trailing_stop_price


@dataclass
class DailyStats:
    date: date = field(default_factory=date.today)
    total_trades: int = 0
    total_loss_idr: float = 0.0
    total_profit_idr: float = 0.0
    is_halted: bool = False
    halt_reason: str = ""

    def reset_if_new_day(self):
        if date.today() != self.date:
            logger.info("[RISK] Hari baru — reset statistik harian")
            self.date = date.today()
            self.total_trades = 0
            self.total_loss_idr = 0.0
            self.total_profit_idr = 0.0
            self.is_halted = False
            self.halt_reason = ""


class RiskManager:

    def __init__(self):
        self.cfg         = RISK_CONFIG
        self.daily_stats = DailyStats()
        self.positions: Dict[str, Position] = {}


    def calc_trade_size(self, available_idr: float) -> float:
        pct        = self.cfg["trade_size_pct"]
        min_order  = self.cfg["min_order_idr"]
        trade_size = available_idr * pct

        if trade_size < min_order:
            logger.warning(f"[RISK] Trade size {trade_size:,.0f} IDR di bawah minimum {min_order:,}")
            return 0.0
        return trade_size


    def can_enter_trade(
        self,
        pair: str,
        available_idr: float,
    ) -> tuple[bool, str]:

        self.daily_stats.reset_if_new_day()

        if self.daily_stats.is_halted:
            return False, f"Circuit breaker aktif: {self.daily_stats.halt_reason}"

        if pair in self.positions:
            return False, f"Sudah ada posisi terbuka di {pair}"

        max_trades = self.cfg["max_trades_per_day"]
        if self.daily_stats.total_trades >= max_trades:
            return False, f"Batas max trade harian ({max_trades}) tercapai"

        trade_size = self.calc_trade_size(available_idr)
        if trade_size <= 0:
            return False, f"Saldo tidak cukup ({available_idr:,.0f} IDR)"

        return True, "OK"


    def check_exit_signals(
        self,
        pair: str,
        current_price: float,
    ) -> tuple[bool, str]:
        
        pos = self.positions.get(pair)
        if not pos:
            return False, "Tidak ada posisi"

        if current_price <= pos.stop_loss:
            return True, f"STOP LOSS @ {current_price:,.0f} (entry: {pos.entry_price:,.0f})"

        if current_price >= pos.take_profit:
            return True, f"TAKE PROFIT @ {current_price:,.0f} (entry: {pos.entry_price:,.0f})"

        if pos.update_trailing_stop(current_price):
            return True, f"TRAILING STOP @ {current_price:,.0f} (trail: {pos.trailing_stop_price:,.0f})"

        return False, ""


    def open_position(
        self,
        pair: str,
        entry_price: float,
        coin_amount: float,
        idr_invested: float,
        order_id: str = "",
    ) -> Position:
        pos = Position(
            pair=pair,
            entry_price=entry_price,
            coin_amount=coin_amount,
            idr_invested=idr_invested,
            order_id=order_id,
        )
        self.positions[pair] = pos
        self.daily_stats.total_trades += 1

        logger.info(
            f"[RISK] Posisi dibuka: {pair} | "
            f"Entry={entry_price:,.0f} | "
            f"SL={pos.stop_loss:,.0f} | "
            f"TP={pos.take_profit:,.0f}"
        )
        return pos

    def close_position(
        self,
        pair: str,
        exit_price: float,
    ) -> Optional[dict]:

        pos = self.positions.pop(pair, None)
        if not pos:
            return None

        idr_received = pos.coin_amount * exit_price
        pnl_idr      = idr_received - pos.idr_invested
        pnl_pct      = pnl_idr / pos.idr_invested * 100
        duration     = (datetime.now() - pos.entry_time).seconds // 60

        if pnl_idr < 0:
            self.daily_stats.total_loss_idr += abs(pnl_idr)
            self._check_circuit_breaker()
        else:
            self.daily_stats.total_profit_idr += pnl_idr

        result = {
            "pair": pair,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "coin_amount": pos.coin_amount,
            "idr_invested": pos.idr_invested,
            "idr_received": idr_received,
            "pnl_idr": pnl_idr,
            "pnl_pct": pnl_pct,
            "duration_min": duration,
            "entry_time": pos.entry_time,
            "exit_time": datetime.now(),
        }

        emoji = "✅" if pnl_idr >= 0 else "❌"
        logger.info(
            f"[RISK] {emoji} Posisi ditutup: {pair} | "
            f"PnL = {pnl_idr:+,.0f} IDR ({pnl_pct:+.2f}%) | "
            f"Durasi: {duration} menit"
        )
        return result

    def _check_circuit_breaker(self):
        max_loss = self.cfg["max_daily_loss_idr"]
        if self.daily_stats.total_loss_idr >= max_loss:
            self.daily_stats.is_halted = True
            self.daily_stats.halt_reason = (
                f"Loss harian {self.daily_stats.total_loss_idr:,.0f} IDR "
                f"melampaui batas {max_loss:,.0f} IDR"
            )
            logger.critical(
                f"[RISK] ⚠️  CIRCUIT BREAKER AKTIF! {self.daily_stats.halt_reason}"
            )


    def get_status(self) -> dict:
        return {
            "halted": self.daily_stats.is_halted,
            "halt_reason": self.daily_stats.halt_reason,
            "trades_today": self.daily_stats.total_trades,
            "loss_today_idr": self.daily_stats.total_loss_idr,
            "profit_today_idr": self.daily_stats.total_profit_idr,
            "open_positions": list(self.positions.keys()),
            "daily_pnl": self.daily_stats.total_profit_idr - self.daily_stats.total_loss_idr,
        }
