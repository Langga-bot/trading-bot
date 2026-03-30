import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import pandas as pd

from indicators import TechnicalIndicators, IndicatorResult
from config import STRATEGY_CONFIG, RISK_CONFIG

logger = logging.getLogger(__name__)


class Signal(Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class StrategySignal:
    name: str
    signal: Signal
    strength: float = 0.0
    reason: str = ""


@dataclass
class FinalDecision:
    action: Signal = Signal.HOLD
    confidence: float = 0.0
    strategies_agreed: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    indicator_result: Optional[IndicatorResult] = None


class TrendFollowingStrategy:

    NAME = "TrendFollowing"

    def evaluate(self, ind: IndicatorResult) -> StrategySignal:
        buy_conditions  = []
        sell_conditions = []

        if ind.ema_fast > ind.ema_slow:
            buy_conditions.append("EMA7>EMA25")
        else:
            sell_conditions.append("EMA7<EMA25")

        if ind.ema_cross == "golden":
            buy_conditions.append("GoldenCross")
        elif ind.ema_cross == "death":
            sell_conditions.append("DeathCross")

        if ind.rsi_signal == "oversold":
            buy_conditions.append(f"RSI_oversold({ind.rsi:.1f})")
        elif ind.rsi_signal == "overbought":
            sell_conditions.append(f"RSI_overbought({ind.rsi:.1f})")
        elif ind.rsi < 60:
            buy_conditions.append(f"RSI_ok({ind.rsi:.1f})")
        else:
            sell_conditions.append(f"RSI_high({ind.rsi:.1f})")

        if ind.macd_direction == "bullish" or ind.macd_hist > 0:
            buy_conditions.append("MACD_bullish")
        elif ind.macd_direction == "bearish" or ind.macd_hist < 0:
            sell_conditions.append("MACD_bearish")

        if ind.trend == "uptrend":
            buy_conditions.append("Uptrend")
        elif ind.trend == "downtrend":
            sell_conditions.append("Downtrend")

        n_buy  = len(buy_conditions)
        n_sell = len(sell_conditions)

        if n_buy >= 3 and n_buy > n_sell:
            strength = min(n_buy / 5.0, 1.0)
            return StrategySignal(
                name=self.NAME,
                signal=Signal.BUY,
                strength=strength,
                reason=" | ".join(buy_conditions),
            )
        elif n_sell >= 3 and n_sell > n_buy:
            strength = min(n_sell / 5.0, 1.0)
            return StrategySignal(
                name=self.NAME,
                signal=Signal.SELL,
                strength=strength,
                reason=" | ".join(sell_conditions),
            )
        else:
            return StrategySignal(
                name=self.NAME,
                signal=Signal.HOLD,
                reason=f"Tidak cukup konfirmasi (buy={n_buy}, sell={n_sell})",
            )


class ScalpingStrategy:

    NAME = "Scalping"

    def evaluate(self, ind: IndicatorResult) -> StrategySignal:
        if not ind.volume_spike:
            return StrategySignal(
                name=self.NAME,
                signal=Signal.HOLD,
                reason="Tidak ada volume spike",
            )

        price_above_ema = ind.ema_fast > ind.ema_slow
        macd_positive   = ind.macd_hist > 0
        rsi_not_ob      = ind.rsi < STRATEGY_CONFIG["rsi_overbought"]

        if price_above_ema and macd_positive and rsi_not_ob:
            return StrategySignal(
                name=self.NAME,
                signal=Signal.BUY,
                strength=0.75,
                reason=f"Volume spike {ind.volume_current/ind.volume_avg:.1f}x + EMA bullish + MACD+",
            )

        price_below_ema = ind.ema_fast < ind.ema_slow
        macd_negative   = ind.macd_hist < 0
        if price_below_ema and macd_negative:
            return StrategySignal(
                name=self.NAME,
                signal=Signal.SELL,
                strength=0.7,
                reason=f"Volume spike {ind.volume_current/ind.volume_avg:.1f}x + EMA bearish + MACD-",
            )

        return StrategySignal(
            name=self.NAME,
            signal=Signal.HOLD,
            reason="Volume spike tapi tidak ada konfirmasi arah",
        )


class MeanReversionStrategy:

    NAME = "MeanReversion"

    def evaluate(self, ind: IndicatorResult) -> StrategySignal:
        rsi_os = STRATEGY_CONFIG["rsi_oversold"]
        rsi_ob = STRATEGY_CONFIG["rsi_overbought"]

        if ind.bb_signal in ("lower_touch", "near_lower"):
            rsi_ok = ind.rsi <= rsi_os + 10 
            strength = 0.9 if ind.bb_signal == "lower_touch" and ind.rsi <= rsi_os else 0.6
            if rsi_ok:
                return StrategySignal(
                    name=self.NAME,
                    signal=Signal.BUY,
                    strength=strength,
                    reason=f"BB lower touch | RSI={ind.rsi:.1f} | Reversion BUY",
                )

        if ind.bb_signal in ("upper_touch", "near_upper"):
            rsi_ok = ind.rsi >= rsi_ob - 10
            strength = 0.9 if ind.bb_signal == "upper_touch" and ind.rsi >= rsi_ob else 0.6
            if rsi_ok:
                return StrategySignal(
                    name=self.NAME,
                    signal=Signal.SELL,
                    strength=strength,
                    reason=f"BB upper touch | RSI={ind.rsi:.1f} | Reversion SELL",
                )

        return StrategySignal(
            name=self.NAME,
            signal=Signal.HOLD,
            reason=f"BB neutral ({ind.bb_signal}) | RSI={ind.rsi:.1f}",
        )


class StrategyEngine:

    def __init__(self):
        self.indicators = TechnicalIndicators()
        self.strategies = [
            TrendFollowingStrategy(),
            ScalpingStrategy(),
            MeanReversionStrategy(),
        ]
        self.min_buy_score  = STRATEGY_CONFIG["min_buy_score"]
        self.min_sell_score = STRATEGY_CONFIG["min_sell_score"]

    def analyze_and_decide(
        self,
        candles: List[dict],
        depth: Optional[dict] = None,
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
            sig = strategy.evaluate(ind)
            signals.append(sig)
            logger.debug(f"[STRATEGY] {sig.name}: {sig.signal.value} | {sig.reason}")

        buy_score  = 0.0
        sell_score = 0.0
        buy_agreed  = []
        sell_agreed = []

        for sig in signals:
            if sig.signal == Signal.BUY:
                buy_score += sig.strength * 3.33
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
            f"[STRATEGY] Buy Score={buy_score:.1f} | "
            f"Sell Score={sell_score:.1f} | "
            f"RSI={ind.rsi:.1f} | Trend={ind.trend}"
        )

        if buy_score >= self.min_buy_score and buy_score > sell_score:
            decision.action      = Signal.BUY
            decision.confidence  = min(buy_score / 10, 1.0)
            decision.strategies_agreed = buy_agreed
        elif sell_score >= self.min_sell_score and sell_score > buy_score:
            decision.action      = Signal.SELL
            decision.confidence  = min(sell_score / 10, 1.0)
            decision.strategies_agreed = sell_agreed
        else:
            decision.action = Signal.HOLD

        return decision

    def get_signal_summary(self, decision: FinalDecision) -> str:
        ind = decision.indicator_result
        if not ind:
            return "No indicator data"
        return (
            f"Action={decision.action.value} | "
            f"Confidence={decision.confidence:.0%} | "
            f"EMA({ind.ema_fast:.0f}/{ind.ema_slow:.0f}) | "
            f"RSI={ind.rsi:.1f} | "
            f"MACD={ind.macd_hist:.4f} | "
            f"BB={ind.bb_signal} | "
            f"Trend={ind.trend} | "
            f"Vol_spike={ind.volume_spike}"
        )
