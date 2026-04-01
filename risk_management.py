import logging
from datetime import datetime, date
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field

from config import RISK_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class Position:
    pair:         str
    entry_price:  float
    coin_amount:  float
    idr_invested: float
    slot_size:    float
    entry_time:   datetime = field(default_factory=datetime.now)
    stop_loss:    float    = 0.0
    take_profit:  float    = 0.0
    trailing_stop_price: float = 0.0
    highest_price:       float = 0.0
    order_id:     str = ""

    def __post_init__(self):
        if self.stop_loss == 0.0:
            self.stop_loss    = self.entry_price * (1 - RISK_CONFIG["stop_loss_pct"])
        if self.take_profit == 0.0:
            self.take_profit  = self.entry_price * (1 + RISK_CONFIG["take_profit_pct"])
        if self.trailing_stop_price == 0.0:
            self.trailing_stop_price = self.stop_loss
        self.highest_price = self.entry_price

    def update_trailing_stop(self, current_price: float) -> bool:
        trail_pct = RISK_CONFIG["trailing_stop_pct"]
        if current_price > self.highest_price:
            self.highest_price       = current_price
            self.trailing_stop_price = current_price * (1 - trail_pct)
        return current_price <= self.trailing_stop_price

    @property
    def unrealized_pct(self, current_price: float = 0) -> float:
        if current_price and self.entry_price:
            return (current_price - self.entry_price) / self.entry_price * 100
        return 0.0


@dataclass
class DailyStats:
    date:             date  = field(default_factory=date.today)
    total_trades:     int   = 0
    total_loss_idr:   float = 0.0
    total_profit_idr: float = 0.0
    is_halted:        bool  = False
    halt_reason:      str   = ""

    def reset_if_new_day(self):
        if date.today() != self.date:
            logger.info("[RISK] Hari baru - reset statistik harian")
            self.date             = date.today()
            self.total_trades     = 0
            self.total_loss_idr   = 0.0
            self.total_profit_idr = 0.0
            self.is_halted        = False
            self.halt_reason      = ""


class RiskManager:
    def __init__(self):
        self.cfg           = RISK_CONFIG
        self.daily_stats   = DailyStats()
        self.positions: Dict[str, Position] = {}

        # Portfolio state
        self._initial_balance: float = 0.0    # snapshot saldo saat start
        self._slot_size:       float = 0.0    # IDR per slot (fixed)
        self._max_slots:       int   = self.cfg.get("max_open_positions", 4)


    def set_initial_balance(self, balance_idr: float) -> None:
        override = self.cfg.get("initial_balance_idr", 0)
        if override and override > 0:
            balance_idr = float(override)

        self._initial_balance = balance_idr
        self._slot_size       = balance_idr * self.cfg["trade_size_pct"]

        logger.info(
            f"[RISK] Portfolio Slot System aktif:\n"
            f"         Saldo awal  : Rp {balance_idr:,.0f}\n"
            f"         Jumlah slot : {self._max_slots}\n"
            f"         Ukuran slot : Rp {self._slot_size:,.0f} "
            f"({self.cfg['trade_size_pct']*100:.0f}% per slot)\n"
            f"         Total alokasi: Rp {self._slot_size * self._max_slots:,.0f}"
        )


    def calc_trade_size(self, available_idr: float = None) -> float:
        if self._slot_size <= 0:
            if available_idr:
                return available_idr * self.cfg["trade_size_pct"]
            return 0.0

        min_order = self.cfg["min_order_idr"]
        if self._slot_size < min_order:
            logger.warning(
                f"[RISK] Slot size Rp {self._slot_size:,.0f} "
                f"di bawah minimum Rp {min_order:,.0f}"
            )
            return 0.0

        return self._slot_size

    def get_portfolio_summary(self) -> dict:
        slots_used  = len(self.positions)
        slots_free  = max(0, self._max_slots - slots_used)
        idr_deployed = sum(p.idr_invested for p in self.positions.values())
        idr_free     = self._slot_size * slots_free

        return {
            "initial_balance":  self._initial_balance,
            "slot_size":        self._slot_size,
            "max_slots":        self._max_slots,
            "slots_used":       slots_used,
            "slots_free":       slots_free,
            "idr_deployed":     idr_deployed,
            "idr_free_slots":   idr_free,
            "open_pairs":       list(self.positions.keys()),
        }


    def can_enter_trade(
        self,
        pair: str,
        available_idr: float,
    ) -> Tuple[bool, str]:
        self.daily_stats.reset_if_new_day()

        if self.daily_stats.is_halted:
            return False, f"Circuit breaker aktif: {self.daily_stats.halt_reason}"

        if pair in self.positions:
            return False, f"Sudah ada posisi di {pair}"

        slots_used = len(self.positions)
        if slots_used >= self._max_slots:
            used_pairs = ", ".join(self.positions.keys())
            return False, (
                f"Semua {self._max_slots} slot penuh "
                f"({used_pairs})"
            )

        if self.daily_stats.total_trades >= self.cfg["max_trades_per_day"]:
            return False, f"Batas max trade harian ({self.cfg['max_trades_per_day']}) tercapai"

        slot_size = self.calc_trade_size(available_idr)
        if slot_size <= 0:
            return False, (
                f"Slot Rp {self._slot_size:,.0f} di bawah minimum "
                f"Rp {self.cfg['min_order_idr']:,.0f}"
            )
        if available_idr < slot_size:
            return False, (
                f"Saldo tersisa Rp {available_idr:,.0f} "
                f"tidak cukup untuk slot Rp {slot_size:,.0f}"
            )

        return True, "OK"

    def check_exit_signals(
        self,
        pair: str,
        current_price: float,
    ) -> Tuple[bool, str]:
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
            pair         = pair,
            entry_price  = entry_price,
            coin_amount  = coin_amount,
            idr_invested = idr_invested,
            slot_size    = self._slot_size,
            order_id     = order_id,
        )
        self.positions[pair] = pos
        self.daily_stats.total_trades += 1

        slots_used = len(self.positions)
        portfolio  = self.get_portfolio_summary()

        logger.info(
            f"[RISK] Slot [{slots_used}/{self._max_slots}] dibuka: {pair}\n"
            f"         Entry       : Rp {entry_price:,.0f}\n"
            f"         Slot size   : Rp {idr_invested:,.0f}\n"
            f"         SL          : Rp {pos.stop_loss:,.0f}\n"
            f"         TP          : Rp {pos.take_profit:,.0f}\n"
            f"         Slot tersisa: {portfolio['slots_free']}"
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

        slots_free = self._max_slots - len(self.positions)
        status     = "PROFIT" if pnl_idr >= 0 else "LOSS"
        logger.info(
            f"[RISK] Slot dibebaskan [{status}]: {pair}\n"
            f"         PnL       : Rp {pnl_idr:+,.0f} ({pnl_pct:+.2f}%)\n"
            f"         Durasi    : {duration} menit\n"
            f"         Slot bebas: {slots_free}/{self._max_slots}"
        )

        return {
            "pair":        pair,
            "entry_price": pos.entry_price,
            "exit_price":  exit_price,
            "coin_amount": pos.coin_amount,
            "idr_invested": pos.idr_invested,
            "idr_received": idr_received,
            "pnl_idr":     pnl_idr,
            "pnl_pct":     pnl_pct,
            "duration_min": duration,
            "entry_time":  pos.entry_time,
            "exit_time":   datetime.now(),
        }

    def _check_circuit_breaker(self):
        max_loss = self.cfg["max_daily_loss_idr"]
        if self.daily_stats.total_loss_idr >= max_loss:
            self.daily_stats.is_halted    = True
            self.daily_stats.halt_reason  = (
                f"Loss harian Rp {self.daily_stats.total_loss_idr:,.0f} "
                f"melampaui batas Rp {max_loss:,.0f}"
            )
            logger.critical(f"[RISK] CIRCUIT BREAKER: {self.daily_stats.halt_reason}")


    def get_status(self) -> dict:
        portfolio = self.get_portfolio_summary()
        return {
            "halted":           self.daily_stats.is_halted,
            "halt_reason":      self.daily_stats.halt_reason,
            "trades_today":     self.daily_stats.total_trades,
            "loss_today_idr":   self.daily_stats.total_loss_idr,
            "profit_today_idr": self.daily_stats.total_profit_idr,
            "open_positions":   list(self.positions.keys()),
            "daily_pnl":        self.daily_stats.total_profit_idr - self.daily_stats.total_loss_idr,
            **portfolio,
        }
