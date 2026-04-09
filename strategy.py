import logging
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import pandas as pd

from indicators import TechnicalIndicators, IndicatorResult
from config import STRATEGY_CONFIG

logger = logging.getLogger(__name__)

FEE_PCT         = STRATEGY_CONFIG.get("taker_fee_pct", 0.003)
MIN_PROFIT_SELL = STRATEGY_CONFIG.get("min_profit_to_sell_pct", 0.008)
MAX_RSI_BUY     = STRATEGY_CONFIG.get("max_rsi_for_buy", 65)
MIN_RSI_SELL    = STRATEGY_CONFIG.get("min_rsi_for_sell", 45)
MIN_BB_WIDTH    = STRATEGY_CONFIG.get("min_bb_width", 0.008)
COOLDOWN_MIN    = STRATEGY_CONFIG.get("buy_cooldown_minutes", 30)


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
    action:            Signal              = Signal.HOLD
    confidence:        float               = 0.0
    strategies_agreed: List[str]           = field(default_factory=list)
    reasons:           List[str]           = field(default_factory=list)
    indicator_result:  Optional[IndicatorResult] = None
    blocked_reason:    str                 = ""


class TrendFollowingStrategy:
    """
    Strategi 1: Trend Following
    Sinyal berdasarkan arah trend jangka menengah.
    BUY butuh 3 dari 4 kondisi (sebelumnya 4/5 — terlalu ketat).
    """
    NAME = "TrendFollowing"

    def evaluate(self, ind: IndicatorResult) -> StrategySignal:
        buy_cond  = []
        sell_cond = []

        # 1. Posisi EMA
        if ind.ema_fast > ind.ema_slow:
            buy_cond.append("EMA_bull")
        else:
            sell_cond.append("EMA_bear")

        # 2. Cross event (sinyal kuat — bobot ekstra)
        if ind.ema_cross == "golden":
            buy_cond.extend(["GoldenCross", "GoldenCross_bonus"])
        elif ind.ema_cross == "death":
            sell_cond.extend(["DeathCross", "DeathCross_bonus"])

        # 3. RSI
        if ind.rsi_signal == "oversold" or ind.rsi < 40:
            buy_cond.append(f"RSI_low({ind.rsi:.0f})")
        elif ind.rsi_signal == "overbought" or ind.rsi > 70:
            sell_cond.append(f"RSI_high({ind.rsi:.0f})")
        elif ind.rsi < 55:
            buy_cond.append(f"RSI_ok({ind.rsi:.0f})")
        else:
            sell_cond.append(f"RSI_elev({ind.rsi:.0f})")

        # 4. MACD
        if ind.macd_hist > 0:
            buy_cond.append("MACD+")
        elif ind.macd_hist < 0:
            sell_cond.append("MACD-")

        # 5. Trend arah
        if ind.trend == "uptrend":
            buy_cond.append("Uptrend")
        elif ind.trend == "downtrend":
            sell_cond.append("Downtrend")

        n_buy  = len(buy_cond)
        n_sell = len(sell_cond)

        # BUY butuh 3 dari 4+ kondisi
        if n_buy >= 3 and n_buy > n_sell:
            return StrategySignal(
                self.NAME, Signal.BUY,
                strength=min(n_buy / 5.0, 1.0),
                reason=" | ".join(buy_cond),
            )
        elif n_sell >= 3 and n_sell > n_buy:
            return StrategySignal(
                self.NAME, Signal.SELL,
                strength=min(n_sell / 5.0, 1.0),
                reason=" | ".join(sell_cond),
            )
        return StrategySignal(
            self.NAME, Signal.HOLD,
            reason=f"Konfirmasi kurang (buy={n_buy}, sell={n_sell})",
        )


class MomentumStrategy:
    """
    Strategi 2: Momentum
    Cari kondisi momentum yang sedang membangun, bukan yang sudah terlambat.
    Bekerja di SEMUA kondisi pasar (tidak hanya sideways).
    """
    NAME = "Momentum"

    def evaluate(self, ind: IndicatorResult) -> StrategySignal:
        score_b = 0
        score_s = 0
        rsn_b   = []
        rsn_s   = []

        # MACD crossover = momentum terkuat
        if ind.macd_direction == "bullish" and ind.macd_hist > 0:
            score_b += 2
            rsn_b.append("MACD_bull")
        elif ind.macd_direction == "bearish" and ind.macd_hist < 0:
            score_s += 2
            rsn_s.append("MACD_bear")

        # RSI momentum zone
        if 25 <= ind.rsi <= 50:
            score_b += 1
            rsn_b.append(f"RSI_zone({ind.rsi:.0f})")
        elif ind.rsi > 65:
            score_s += 1
            rsn_s.append(f"RSI_high({ind.rsi:.0f})")

        # Bollinger Band position
        if ind.bb_signal in ("lower_touch", "near_lower"):
            score_b += 2
            rsn_b.append(f"BB_{ind.bb_signal}")
        elif ind.bb_signal in ("upper_touch", "near_upper"):
            score_s += 2
            rsn_s.append(f"BB_{ind.bb_signal}")

        # Volume konfirmasi arah
        if ind.volume_spike:
            if ind.ema_fast > ind.ema_slow:
                score_b += 1
                rsn_b.append("Vol_bull")
            else:
                score_s += 1
                rsn_s.append("Vol_bear")

        if score_b >= 3 and score_b > score_s:
            return StrategySignal(
                self.NAME, Signal.BUY,
                strength=min(score_b / 6.0, 1.0),
                reason=" | ".join(rsn_b),
            )
        elif score_s >= 3 and score_s > score_b:
            return StrategySignal(
                self.NAME, Signal.SELL,
                strength=min(score_s / 6.0, 1.0),
                reason=" | ".join(rsn_s),
            )
        return StrategySignal(
            self.NAME, Signal.HOLD,
            reason=f"Momentum lemah (b={score_b}, s={score_s})",
        )


class MeanReversionStrategy:
    """
    Strategi 3: Mean Reversion
    Aktif di SEMUA kondisi (bukan hanya sideways).
    Berbeda dari versi lama: tidak memblokir diri sendiri saat trending.
    Hanya beli saat kondisi sangat oversold, hanya jual saat sangat overbought.
    """
    NAME = "MeanReversion"

    def evaluate(self, ind: IndicatorResult) -> StrategySignal:
        rsi_os = STRATEGY_CONFIG["rsi_oversold"]
        rsi_ob = STRATEGY_CONFIG["rsi_overbought"]

        # BUY: BB lower touch + RSI oversold (syarat keduanya)
        if ind.bb_signal == "lower_touch" and ind.rsi <= rsi_os + 8:
            return StrategySignal(
                self.NAME, Signal.BUY, strength=0.9,
                reason=f"BB lower | RSI={ind.rsi:.0f}",
            )
        if ind.bb_signal == "near_lower" and ind.rsi <= rsi_os + 3:
            return StrategySignal(
                self.NAME, Signal.BUY, strength=0.7,
                reason=f"BB near_lower | RSI={ind.rsi:.0f}",
            )

        # SELL: BB upper touch + RSI overbought (syarat keduanya)
        if ind.bb_signal == "upper_touch" and ind.rsi >= rsi_ob - 8:
            return StrategySignal(
                self.NAME, Signal.SELL, strength=0.9,
                reason=f"BB upper | RSI={ind.rsi:.0f}",
            )
        if ind.bb_signal == "near_upper" and ind.rsi >= rsi_ob - 3:
            return StrategySignal(
                self.NAME, Signal.SELL, strength=0.7,
                reason=f"BB near_upper | RSI={ind.rsi:.0f}",
            )

        return StrategySignal(
            self.NAME, Signal.HOLD,
            reason=f"BB={ind.bb_signal} RSI={ind.rsi:.0f}",
        )


class StrategyEngine:
    """
    Engine voting dengan filter kualitas.

    Urutan pemeriksaan BUY:
      1. Hitung skor dari 3 strategi
      2. Cek buy_score >= 7.5
      3. Filter RSI (tidak terlalu tinggi)
      4. Filter BB width (volatilitas cukup)
      5. Filter cooldown
      6. Konfirmasi candle sebelumnya (bukan multi-analysis berat)

    Urutan pemeriksaan SELL via strategi:
      1. Hitung skor
      2. Filter profit bersih setelah fee
      3. Filter RSI (tidak jual saat oversold)
    """

    def __init__(self):
        self.indicators   = TechnicalIndicators()
        self.strategies   = [
            TrendFollowingStrategy(),
            MomentumStrategy(),
            MeanReversionStrategy(),
        ]
        self.min_buy_score  = STRATEGY_CONFIG["min_buy_score"]
        self.min_sell_score = STRATEGY_CONFIG["min_sell_score"]
        self._last_sell_time: Dict[str, datetime] = {}

    def record_sell(self, pair: str) -> None:
        """Dipanggil dari main.py setelah SELL. Aktifkan cooldown."""
        self._last_sell_time[pair] = datetime.now()
        logger.info(f"[STRATEGY] Cooldown {COOLDOWN_MIN} menit dimulai: {pair}")

    def _in_cooldown(self, pair: str) -> tuple:
        """Return (True, menit_tersisa) atau (False, 0)."""
        last = self._last_sell_time.get(pair)
        if not last:
            return False, 0
        elapsed   = (datetime.now() - last).total_seconds() / 60
        remaining = COOLDOWN_MIN - elapsed
        if remaining > 0:
            return True, remaining
        return False, 0

    def _prev_candle_confirms_buy(self, df: pd.DataFrame) -> bool:
        """
        Cek candle ke-2 dari belakang untuk konfirmasi.
        Ringan: hanya cek apakah kondisi dasar sudah terpenuhi di candle sebelumnya.
        Tidak re-run full analysis — hanya cek harga dan indikator sederhana.
        """
        if len(df) < 35:
            return True   # tidak cukup data, skip konfirmasi

        try:
            # Ambil subset tanpa candle terakhir
            df_prev = df.iloc[:-1]
            ind_prev = self.indicators.analyze(df_prev)

            # Konfirmasi minimal: EMA masih bullish di candle sebelumnya
            ema_ok  = ind_prev.ema_fast >= ind_prev.ema_slow * 0.999
            rsi_ok  = ind_prev.rsi < MAX_RSI_BUY + 5
            # Tidak butuh MACD positif — terlalu ketat

            if not ema_ok:
                logger.debug(
                    f"[STRATEGY] Prev candle EMA bearish "
                    f"({ind_prev.ema_fast:.0f} < {ind_prev.ema_slow:.0f})"
                )
                return False
            if not rsi_ok:
                logger.debug(f"[STRATEGY] Prev candle RSI tinggi ({ind_prev.rsi:.0f})")
                return False
            return True
        except Exception as e:
            logger.debug(f"[STRATEGY] prev_candle_check error: {e}")
            return True   # jika error, tidak blokir

    def analyze_and_decide(
        self,
        candles:       List[dict],
        depth:         Optional[dict] = None,
        pair:          str            = "",
        current_price: float          = 0,
        entry_price:   float          = 0,
    ) -> FinalDecision:
        """Pipeline utama analisis dan keputusan."""
        decision = FinalDecision()

        df = self.indicators.prepare_dataframe(candles)
        if df.empty or len(df) < 30:
            decision.reasons.append("Data candle tidak cukup")
            return decision

        ind = self.indicators.analyze(df, depth)
        decision.indicator_result = ind

        # ── Jalankan 3 strategi ──
        signals: List[StrategySignal] = []
        for strat in self.strategies:
            try:
                sig = strat.evaluate(ind)
                signals.append(sig)
                logger.debug(f"[STRATEGY] {sig.name}: {sig.signal.value} | {sig.reason}")
            except Exception as e:
                logger.warning(f"[STRATEGY] {strat.NAME} error: {e}")

        # ── Hitung skor ──
        buy_score   = 0.0
        sell_score  = 0.0
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

        # Tambah composite score dari indikator
        buy_score  += ind.buy_score
        sell_score += ind.sell_score

        logger.info(
            f"[STRATEGY] Buy={buy_score:.1f} | Sell={sell_score:.1f} | "
            f"RSI={ind.rsi:.1f} | Trend={ind.trend} | BB={ind.bb_signal} | "
            f"EMA({ind.ema_fast:.0f}/{ind.ema_slow:.0f})"
        )

        # ═══ EVALUASI BUY ════════════════════════════════════════════════════
        if buy_score >= self.min_buy_score and buy_score > sell_score:
            block = self._filter_buy(ind, df, pair)
            if block:
                decision.blocked_reason = block
                decision.action         = Signal.HOLD
                logger.info(f"[STRATEGY] BUY diblokir [{pair}]: {block}")
            else:
                decision.action            = Signal.BUY
                decision.confidence        = min(buy_score / 10, 1.0)
                decision.strategies_agreed = buy_agreed

        # ═══ EVALUASI SELL via strategi ══════════════════════════════════════
        elif sell_score >= self.min_sell_score and sell_score > buy_score:
            block = self._filter_sell(ind, pair, current_price, entry_price)
            if block:
                decision.blocked_reason = block
                decision.action         = Signal.HOLD
                logger.info(f"[STRATEGY] SELL diblokir [{pair}]: {block}")
            else:
                decision.action            = Signal.SELL
                decision.confidence        = min(sell_score / 10, 1.0)
                decision.strategies_agreed = sell_agreed
        else:
            decision.action = Signal.HOLD

        return decision

    def _filter_buy(
        self, ind: IndicatorResult, df: pd.DataFrame, pair: str
    ) -> str:
        """
        Filter kualitas untuk BUY.
        Return: string alasan blokir jika ada, '' jika lolos semua.
        """

        # 1. RSI terlalu tinggi — risiko beli di puncak
        if ind.rsi > MAX_RSI_BUY:
            return f"RSI {ind.rsi:.0f} terlalu tinggi (max {MAX_RSI_BUY})"

        # 2. Volatilitas terlalu rendah
        if ind.bb_width < MIN_BB_WIDTH:
            return f"Volatilitas terlalu rendah (BB width {ind.bb_width:.4f} < {MIN_BB_WIDTH})"

        # 3. Cooldown setelah sell
        in_cd, remaining = self._in_cooldown(pair)
        if in_cd:
            return f"Cooldown aktif: {remaining:.0f} menit lagi"

        # 4. Konfirmasi candle sebelumnya (ringan)
        if not self._prev_candle_confirms_buy(df):
            return "Candle sebelumnya tidak konfirmasi (EMA atau RSI bermasalah)"

        return ""   # lolos semua filter

    def _filter_sell(
        self, ind: IndicatorResult,
        pair: str, current_price: float, entry_price: float
    ) -> str:
        """
        Filter untuk SELL via strategi.
        SL/TP TIDAK melewati filter ini — langsung eksekusi.
        """
        # 1. Profit bersih belum menutup fee
        if entry_price > 0 and current_price > 0:
            gross_pct = (current_price - entry_price) / entry_price
            net_pct   = gross_pct - (FEE_PCT * 2)
            if net_pct < MIN_PROFIT_SELL:
                return (
                    f"Profit bersih {net_pct*100:.2f}% "
                    f"belum cukup (min {MIN_PROFIT_SELL*100:.1f}% setelah fee)"
                )

        # 2. RSI terlalu rendah — kondisi oversold, potensi reversal naik
        if ind.rsi < MIN_RSI_SELL:
            return f"RSI {ind.rsi:.0f} terlalu rendah untuk strategy sell"

        return ""

    def get_signal_summary(self, decision: FinalDecision) -> str:
        ind = decision.indicator_result
        if not ind:
            return "No data"
        blk = f" | BLOCKED: {decision.blocked_reason}" if decision.blocked_reason else ""
        return (
            f"Action={decision.action.value} | "
            f"Confidence={decision.confidence:.0%} | "
            f"EMA({ind.ema_fast:.0f}/{ind.ema_slow:.0f}) | "
            f"RSI={ind.rsi:.1f} | MACD={ind.macd_hist:.4f} | "
            f"BB={ind.bb_signal} | Trend={ind.trend} | "
            f"Vol={ind.volume_spike}"
            f"{blk}"
        )
