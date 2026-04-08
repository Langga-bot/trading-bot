import logging
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import pandas as pd

from indicators import TechnicalIndicators, IndicatorResult
from config import STRATEGY_CONFIG, RISK_CONFIG

logger = logging.getLogger(__name__)

FEE_PCT         = STRATEGY_CONFIG.get("taker_fee_pct", 0.003)
MIN_PROFIT_SELL = STRATEGY_CONFIG.get("min_profit_to_sell_pct", 0.008)
MAX_RSI_BUY     = STRATEGY_CONFIG.get("max_rsi_for_buy", 65)
MIN_RSI_SELL    = STRATEGY_CONFIG.get("min_rsi_for_sell", 45)
MIN_BB_WIDTH    = STRATEGY_CONFIG.get("min_bb_width", 0.01)
MIN_TREND_STR   = STRATEGY_CONFIG.get("min_trend_strength", 0.08)
CONFIRM_CANDLES = STRATEGY_CONFIG.get("signal_confirm_candles", 3)


class Signal(Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class StrategySignal:
    name:     str
    signal:   Signal
    strength: float = 0.0
    reason:   str   = ""


@dataclass
class FinalDecision:
    action:             Signal            = Signal.HOLD
    confidence:         float             = 0.0
    strategies_agreed:  List[str]         = field(default_factory=list)
    reasons:            List[str]         = field(default_factory=list)
    indicator_result:   Optional[IndicatorResult] = None
    blocked_reason:     str               = ""


class TrendFollowingStrategy:
    """
    Strategi 1: Trend Following
    Lebih ketat — butuh 4 dari 5 kondisi untuk BUY (sebelumnya 3 dari 5).
    Tambah konfirmasi: trend harus uptrend, bukan hanya EMA cross.
    """
    NAME = "TrendFollowing"

    def evaluate(self, ind: IndicatorResult, df: pd.DataFrame = None) -> StrategySignal:
        buy  = []
        sell = []

        if ind.ema_fast > ind.ema_slow:
            buy.append("EMA7>EMA25")
        else:
            sell.append("EMA7<EMA25")

        if ind.ema_cross == "golden":
            buy.append("GoldenCross")
        elif ind.ema_cross == "death":
            sell.append("DeathCross")

        if ind.rsi_signal == "oversold":
            buy.append(f"RSI_oversold({ind.rsi:.0f})")
        elif ind.rsi_signal == "overbought":
            sell.append(f"RSI_overbought({ind.rsi:.0f})")
        elif ind.rsi < 50:
            buy.append(f"RSI_ok({ind.rsi:.0f})")
        else:
            sell.append(f"RSI_high({ind.rsi:.0f})")

        if ind.macd_direction == "bullish" or ind.macd_hist > 0:
            buy.append("MACD+")
        elif ind.macd_direction == "bearish" or ind.macd_hist < 0:
            sell.append("MACD-")

        if ind.trend == "uptrend":
            buy.append("Uptrend")
        elif ind.trend == "downtrend":
            sell.append("Downtrend")
        else:
            sell.append("Sideways")   # sideways tidak ideal untuk trend following

        n_buy  = len(buy)
        n_sell = len(sell)

        # BUY butuh ≥4 dari 5 kondisi (lebih ketat dari sebelumnya)
        if n_buy >= 4 and n_buy > n_sell:
            return StrategySignal(
                self.NAME, Signal.BUY,
                strength=min(n_buy / 5.0, 1.0),
                reason=" | ".join(buy),
            )
        elif n_sell >= 4 and n_sell > n_buy:
            return StrategySignal(
                self.NAME, Signal.SELL,
                strength=min(n_sell / 5.0, 1.0),
                reason=" | ".join(sell),
            )
        return StrategySignal(
            self.NAME, Signal.HOLD,
            reason=f"Tidak cukup konfirmasi (buy={n_buy}, sell={n_sell})",
        )


class MomentumStrategy:
    NAME = "Momentum"

    def evaluate(self, ind: IndicatorResult, df: pd.DataFrame = None) -> StrategySignal:
        score_buy  = 0
        score_sell = 0
        reasons_b  = []
        reasons_s  = []

        # MACD crossover = momentum kuat (bobot tinggi)
        if hasattr(ind, '_macd_crossover') and ind._macd_crossover:
            score_buy += 2
            reasons_b.append("MACD_crossover")
        elif ind.macd_hist > 0 and ind.macd_direction == "bullish":
            score_buy += 1
            reasons_b.append("MACD_bullish")

        if hasattr(ind, '_macd_crossunder') and ind._macd_crossunder:
            score_sell += 2
            reasons_s.append("MACD_crossunder")
        elif ind.macd_hist < 0 and ind.macd_direction == "bearish":
            score_sell += 1
            reasons_s.append("MACD_bearish")

        # RSI momentum
        if 30 <= ind.rsi <= 50:
            score_buy += 1
            reasons_b.append(f"RSI_rising({ind.rsi:.0f})")
        elif ind.rsi >= 65:
            score_sell += 1
            reasons_s.append(f"RSI_high({ind.rsi:.0f})")

        # Bollinger Band position
        if ind.bb_signal in ("lower_touch", "near_lower"):
            score_buy += 1
            reasons_b.append(f"BB_{ind.bb_signal}")
        elif ind.bb_signal in ("upper_touch", "near_upper"):
            score_sell += 1
            reasons_s.append(f"BB_{ind.bb_signal}")

        # Volume konfirmasi
        if ind.volume_spike and ind.trend == "uptrend":
            score_buy += 1
            reasons_b.append("Volume_spike_up")
        elif ind.volume_spike and ind.trend == "downtrend":
            score_sell += 1
            reasons_s.append("Volume_spike_down")

        # Butuh skor ≥3 untuk sinyal
        if score_buy >= 3 and score_buy > score_sell:
            return StrategySignal(
                self.NAME, Signal.BUY,
                strength=min(score_buy / 5.0, 1.0),
                reason=" | ".join(reasons_b),
            )
        elif score_sell >= 3 and score_sell > score_buy:
            return StrategySignal(
                self.NAME, Signal.SELL,
                strength=min(score_sell / 5.0, 1.0),
                reason=" | ".join(reasons_s),
            )
        return StrategySignal(
            self.NAME, Signal.HOLD,
            reason=f"Momentum lemah (buy={score_buy}, sell={score_sell})",
        )


class MeanReversionStrategy:
    NAME = "MeanReversion"

    def evaluate(self, ind: IndicatorResult, df: pd.DataFrame = None) -> StrategySignal:
        # Mean reversion kurang efektif di trending market
        if ind.trend != "sideways":
            return StrategySignal(
                self.NAME, Signal.HOLD,
                reason=f"Trend={ind.trend}, MeanReversion skip",
            )

        rsi_os = STRATEGY_CONFIG["rsi_oversold"]
        rsi_ob = STRATEGY_CONFIG["rsi_overbought"]

        # BUY: harga di lower band + RSI oversold (keduanya harus terpenuhi)
        if ind.bb_signal == "lower_touch" and ind.rsi <= rsi_os + 5:
            return StrategySignal(
                self.NAME, Signal.BUY, strength=0.9,
                reason=f"BB lower touch | RSI={ind.rsi:.0f} (oversold)",
            )
        if ind.bb_signal == "near_lower" and ind.rsi <= rsi_os:
            return StrategySignal(
                self.NAME, Signal.BUY, strength=0.7,
                reason=f"BB near lower | RSI={ind.rsi:.0f} (oversold)",
            )

        # SELL: harga di upper band + RSI overbought (keduanya harus terpenuhi)
        if ind.bb_signal == "upper_touch" and ind.rsi >= rsi_ob - 5:
            return StrategySignal(
                self.NAME, Signal.SELL, strength=0.9,
                reason=f"BB upper touch | RSI={ind.rsi:.0f} (overbought)",
            )
        if ind.bb_signal == "near_upper" and ind.rsi >= rsi_ob:
            return StrategySignal(
                self.NAME, Signal.SELL, strength=0.7,
                reason=f"BB near upper | RSI={ind.rsi:.0f} (overbought)",
            )

        return StrategySignal(
            self.NAME, Signal.HOLD,
            reason=f"BB={ind.bb_signal}, RSI={ind.rsi:.0f}",
        )


class StrategyEngine:
    def __init__(self):
        self.indicators   = TechnicalIndicators()
        self.strategies   = [
            TrendFollowingStrategy(),
            MomentumStrategy(),
            MeanReversionStrategy(),
        ]
        self.min_buy_score  = STRATEGY_CONFIG["min_buy_score"]
        self.min_sell_score = STRATEGY_CONFIG["min_sell_score"]

        # Cooldown tracking: pair -> waktu sell terakhir
        self._last_sell_time: Dict[str, datetime] = {}
        self._cooldown_min = STRATEGY_CONFIG.get("buy_cooldown_minutes", 30)

    def record_sell(self, pair: str) -> None:
        """Dipanggil dari main.py setelah SELL eksekusi."""
        self._last_sell_time[pair] = datetime.now()
        logger.info(f"[STRATEGY] Cooldown dimulai untuk {pair} ({self._cooldown_min} menit)")

    def _in_cooldown(self, pair: str) -> bool:
        last = self._last_sell_time.get(pair)
        if not last:
            return False
        elapsed = (datetime.now() - last).total_seconds() / 60
        if elapsed < self._cooldown_min:
            logger.info(
                f"[STRATEGY] {pair} dalam cooldown — {self._cooldown_min - elapsed:.0f} menit lagi"
            )
            return True
        return False

    def _check_multi_candle(
        self, df: pd.DataFrame, signal: Signal, n: int = CONFIRM_CANDLES
    ) -> bool:
        if len(df) < n + 10:
            return False
        try:
            confirms = 0
            for i in range(1, n + 1):
                subset  = df.iloc[:-i] if i > 0 else df
                if len(subset) < 30:
                    break
                ind_sub = self.indicators.analyze(subset)
                if signal == Signal.BUY:
                    is_ok = (ind_sub.ema_fast > ind_sub.ema_slow and
                             ind_sub.rsi < MAX_RSI_BUY and
                             ind_sub.macd_hist > 0)
                else:
                    is_ok = (ind_sub.ema_fast < ind_sub.ema_slow or
                             ind_sub.rsi > 60)
                if is_ok:
                    confirms += 1

            result = confirms >= (n - 1)   # maks 1 candle boleh berbeda
            if not result:
                logger.debug(
                    f"[STRATEGY] Multi-candle gagal: {confirms}/{n} candle konfirmasi"
                )
            return result
        except Exception as e:
            logger.debug(f"[STRATEGY] Multi-candle check error: {e}")
            return False

    def analyze_and_decide(
        self,
        candles: List[dict],
        depth: Optional[dict]    = None,
        pair:   str              = "",
        current_price: float     = 0,
        entry_price:   float     = 0,
    ) -> FinalDecision:
        decision = FinalDecision()

        df = self.indicators.prepare_dataframe(candles)
        if df.empty or len(df) < 30:
            decision.reasons.append("Data tidak cukup untuk analisis")
            return decision

        ind = self.indicators.analyze(df, depth)
        decision.indicator_result = ind

        signals: List[StrategySignal] = []
        for strategy in self.strategies:
            try:
                sig = strategy.evaluate(ind, df)
                signals.append(sig)
                logger.debug(f"[STRATEGY] {sig.name}: {sig.signal.value} | {sig.reason}")
            except Exception as e:
                logger.warning(f"[STRATEGY] {strategy.NAME} error: {e}")

        buy_score  = 0.0
        sell_score = 0.0
        buy_agreed  = []
        sell_agreed = []

        for sig in signals:
            if sig.signal == Signal.BUY:
                buy_score  += sig.strength * 3.33
                buy_agreed.append(sig.name)
                decision.reasons.append(f"[{sig.name}] BUY: {sig.reason}")
            elif sig.signal == Signal.SELL:
                sell_score += sig.strength * 3.33
                sell_agreed.append(sig.name)
                decision.reasons.append(f"[{sig.name}] SELL: {sig.reason}")
            else:
                decision.reasons.append(f"[{sig.name}] HOLD: {sig.reason}")

        buy_score  += ind.buy_score
        sell_score += ind.sell_score

        logger.info(
            f"[STRATEGY] Buy={buy_score:.1f} | Sell={sell_score:.1f} | "
            f"RSI={ind.rsi:.1f} | Trend={ind.trend} | BB={ind.bb_signal}"
        )

        is_buy_candidate = (
            buy_score >= self.min_buy_score and buy_score > sell_score
        )
        if is_buy_candidate:
            block = self._check_buy_filters(ind, df, pair, buy_score)
            if block:
                decision.blocked_reason = block
                logger.info(f"[STRATEGY] BUY diblokir: {block}")
                decision.action = Signal.HOLD
            else:
                decision.action             = Signal.BUY
                decision.confidence         = min(buy_score / 10, 1.0)
                decision.strategies_agreed  = buy_agreed

        elif sell_score >= self.min_sell_score and sell_score > buy_score:
            block = self._check_sell_filters(ind, pair, current_price, entry_price)
            if block:
                decision.blocked_reason = block
                logger.info(f"[STRATEGY] SELL diblokir: {block}")
                decision.action = Signal.HOLD
            else:
                decision.action            = Signal.SELL
                decision.confidence        = min(sell_score / 10, 1.0)
                decision.strategies_agreed = sell_agreed
        else:
            decision.action = Signal.HOLD

        return decision

    def _check_buy_filters(
        self, ind: IndicatorResult, df: pd.DataFrame,
        pair: str, buy_score: float
    ) -> str:
        # 1. RSI terlalu tinggi — jangan beli saat overbought
        if ind.rsi > MAX_RSI_BUY:
            return f"RSI={ind.rsi:.0f} terlalu tinggi (max {MAX_RSI_BUY})"

        # 2. Volatilitas terlalu rendah — tidak ada ruang gerak
        if ind.bb_width < MIN_BB_WIDTH:
            return f"BB width={ind.bb_width:.3f} terlalu sempit (min {MIN_BB_WIDTH})"

        # 3. Trend terlalu lemah di pasar sideways
        if ind.trend == "sideways" and buy_score < 8.0:
            return f"Trend sideways dan skor tidak cukup kuat ({buy_score:.1f} < 8.0)"

        # 4. Harga mendekati resistance — risiko reversal
        if ind.resistance > 0 and ind.ema_fast > 0:
            dist_to_res = (ind.resistance - ind.ema_fast) / ind.ema_fast
            if dist_to_res < 0.005:   # kurang dari 0.5% dari resistance
                return f"Harga terlalu dekat resistance ({dist_to_res*100:.2f}%)"

        # 5. Cooldown setelah sell
        if pair and self._in_cooldown(pair):
            elapsed = (datetime.now() - self._last_sell_time[pair]).total_seconds() / 60
            remaining = self._cooldown_min - elapsed
            return f"Cooldown aktif — {remaining:.0f} menit lagi"

        # 6. Multi-candle confirmation
        if not self._check_multi_candle(df, Signal.BUY):
            return "Multi-candle konfirmasi gagal (sinyal tidak konsisten)"

        return ""   # semua filter lulus

    def _check_sell_filters(
        self, ind: IndicatorResult,
        pair: str, current_price: float, entry_price: float
    ) -> str:

        if entry_price > 0 and current_price > 0:
            gross_pnl_pct = (current_price - entry_price) / entry_price
            net_pnl_pct   = gross_pnl_pct - (FEE_PCT * 2)   # fee beli + jual
            if net_pnl_pct < MIN_PROFIT_SELL:
                return (
                    f"Profit bersih {net_pnl_pct*100:.2f}% "
                    f"belum menutup fee (min {MIN_PROFIT_SELL*100:.1f}%)"
                )

        if ind.rsi < MIN_RSI_SELL:
            return f"RSI={ind.rsi:.0f} terlalu rendah untuk strategy sell"

        return ""

    def get_signal_summary(self, decision: FinalDecision) -> str:
        ind = decision.indicator_result
        if not ind:
            return "No data"
        blocked = f" | BLOCKED: {decision.blocked_reason}" if decision.blocked_reason else ""
        return (
            f"Action={decision.action.value} | "
            f"Confidence={decision.confidence:.0%} | "
            f"EMA({ind.ema_fast:.0f}/{ind.ema_slow:.0f}) | "
            f"RSI={ind.rsi:.1f} | MACD={ind.macd_hist:.4f} | "
            f"BB={ind.bb_signal} | Trend={ind.trend} | "
            f"Vol_spike={ind.volume_spike}"
            f"{blocked}"
        )
