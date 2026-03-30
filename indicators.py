import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from config import STRATEGY_CONFIG


@dataclass
class IndicatorResult:
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    ema_cross: str = "neutral"

    # RSI
    rsi: float = 50.0
    rsi_signal: str = "neutral"

    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    macd_direction: str = "neutral" 

    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_signal: str = "neutral"
    bb_width: float = 0.0

    volume_avg: float = 0.0
    volume_current: float = 0.0
    volume_spike: bool = False

    trend: str = "sideways"

    support: float = 0.0
    resistance: float = 0.0

    buy_score: int = 0
    sell_score: int = 0


class TechnicalIndicators:

    def __init__(self):
        self.cfg = STRATEGY_CONFIG


    @staticmethod
    def prepare_dataframe(candles: List[dict]) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df.set_index("timestamp", inplace=True)
        df = df.astype({"open": float, "high": float, "low": float,
                         "close": float, "volume": float})
        df.sort_index(inplace=True)
        return df


    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def calc_ema_signals(self, df: pd.DataFrame) -> Dict:
        fast = self.cfg["ema_fast"]
        slow = self.cfg["ema_slow"]

        ema_f = self.ema(df["close"], fast)
        ema_s = self.ema(df["close"], slow)

        current_f = ema_f.iloc[-1]
        current_s = ema_s.iloc[-1]
        prev_f    = ema_f.iloc[-2]
        prev_s    = ema_s.iloc[-2]

        if prev_f < prev_s and current_f > current_s:
            cross = "golden"
        elif prev_f > prev_s and current_f < current_s:
            cross = "death"
        else:
            cross = "above" if current_f > current_s else "below"

        return {
            "ema_fast": current_f,
            "ema_slow": current_s,
            "ema_cross": cross,
            "ema_bullish": current_f > current_s,
        }


    def calc_rsi(self, df: pd.DataFrame) -> Dict:
        period = self.cfg["rsi_period"]
        delta  = df["close"].diff()
        gain   = delta.clip(lower=0)
        loss   = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()

        rs  = avg_gain / avg_loss.replace(0, np.inf)
        rsi = 100 - (100 / (1 + rs))

        current = rsi.iloc[-1]
        ob = self.cfg["rsi_overbought"]
        os = self.cfg["rsi_oversold"]

        if current >= ob:
            signal = "overbought"
        elif current <= os:
            signal = "oversold"
        else:
            signal = "neutral"

        return {
            "rsi": current,
            "rsi_signal": signal,
            "rsi_series": rsi,
        }


    def calc_macd(self, df: pd.DataFrame) -> Dict:
        fast   = self.cfg["macd_fast"]
        slow   = self.cfg["macd_slow"]
        signal = self.cfg["macd_signal"]

        ema_f  = self.ema(df["close"], fast)
        ema_s  = self.ema(df["close"], slow)
        macd   = ema_f - ema_s
        sig    = self.ema(macd, signal)
        hist   = macd - sig

        curr_macd = macd.iloc[-1]
        curr_sig  = sig.iloc[-1]
        curr_hist = hist.iloc[-1]
        prev_hist = hist.iloc[-2]

        if curr_hist > 0 and curr_hist > prev_hist:
            direction = "bullish"
        elif curr_hist < 0 and curr_hist < prev_hist:
            direction = "bearish"
        else:
            direction = "neutral"

        return {
            "macd": curr_macd,
            "macd_signal": curr_sig,
            "macd_hist": curr_hist,
            "macd_direction": direction,
            "macd_crossover": prev_hist < 0 < curr_hist,
            "macd_crossunder": prev_hist > 0 > curr_hist,
        }


    def calc_bollinger_bands(self, df: pd.DataFrame) -> Dict:
        period  = self.cfg["bb_period"]
        std_dev = self.cfg["bb_std"]

        middle = df["close"].rolling(period).mean()
        std    = df["close"].rolling(period).std()
        upper  = middle + std_dev * std
        lower  = middle - std_dev * std

        price  = df["close"].iloc[-1]
        u, m, l = upper.iloc[-1], middle.iloc[-1], lower.iloc[-1]
        width  = (u - l) / m if m != 0 else 0

        pct_b  = (price - l) / (u - l) if (u - l) != 0 else 0.5

        if pct_b >= 1.0:
            signal = "upper_touch"
        elif pct_b <= 0.0:
            signal = "lower_touch"
        elif pct_b > 0.8:
            signal = "near_upper"
        elif pct_b < 0.2:
            signal = "near_lower"
        else:
            signal = "neutral"

        return {
            "bb_upper": u,
            "bb_middle": m,
            "bb_lower": l,
            "bb_width": width,
            "bb_pct_b": pct_b,
            "bb_signal": signal,
        }


    def calc_volume(self, df: pd.DataFrame) -> Dict:
        mult   = self.cfg["volume_spike_multiplier"]
        window = 20

        vol_current = df["volume"].iloc[-1]
        vol_avg     = df["volume"].rolling(window).mean().iloc[-1]
        vol_spike   = vol_current > vol_avg * mult

        price_up  = df["close"].iloc[-1] > df["close"].iloc[-2]
        vol_up    = vol_current > df["volume"].iloc[-2]
        vol_confirm = (price_up and vol_up) or (not price_up and not vol_up)

        return {
            "volume_current": vol_current,
            "volume_avg": vol_avg,
            "volume_spike": vol_spike,
            "volume_ratio": vol_current / vol_avg if vol_avg > 0 else 1.0,
            "volume_confirms_trend": vol_confirm,
        }


    def calc_trend(self, df: pd.DataFrame) -> Dict:
        period = 20
        if len(df) < period:
            return {"trend": "sideways", "trend_strength": 0}

        recent = df.tail(period)
        highs  = recent["high"].values
        lows   = recent["low"].values
        closes = recent["close"].values

        # Linear regression slope
        x     = np.arange(len(closes))
        slope = np.polyfit(x, closes, 1)[0]
        # Normalized slope (% per candle)
        norm_slope = slope / closes.mean() * 100

        if norm_slope > 0.05:
            trend = "uptrend"
        elif norm_slope < -0.05:
            trend = "downtrend"
        else:
            trend = "sideways"

        return {
            "trend": trend,
            "trend_strength": abs(norm_slope),
            "trend_slope": norm_slope,
        }


    def calc_support_resistance(self, df: pd.DataFrame) -> Dict:
        window = 20
        recent = df.tail(50)

        lows  = recent["low"]
        highs = recent["high"]

        support    = lows.rolling(window, center=True).min().dropna().iloc[-1]
        resistance = highs.rolling(window, center=True).max().dropna().iloc[-1]

        current = df["close"].iloc[-1]
        near_support    = abs(current - support) / current < 0.01
        near_resistance = abs(current - resistance) / current < 0.01

        return {
            "support": support,
            "resistance": resistance,
            "near_support": near_support,
            "near_resistance": near_resistance,
        }


    @staticmethod
    def analyze_order_book(depth: dict) -> Dict:

        bids = depth.get("buy", [])
        asks = depth.get("sell", [])

        bid_arr = np.array([[float(p), float(v)] for p, v in bids[:20]])
        ask_arr = np.array([[float(p), float(v)] for p, v in asks[:20]])

        if len(bid_arr) == 0 or len(ask_arr) == 0:
            return {"ob_signal": "neutral", "whale_detected": False}

        bid_vol_idr = np.sum(bid_arr[:, 0] * bid_arr[:, 1])
        ask_vol_idr = np.sum(ask_arr[:, 0] * ask_arr[:, 1])

        total     = bid_vol_idr + ask_vol_idr
        bid_ratio = bid_vol_idr / total if total > 0 else 0.5

        if bid_ratio > 0.65:
            signal = "bid_dominant"
        elif bid_ratio < 0.35:
            signal = "ask_dominant"
        else:
            signal = "balanced"

        avg_bid = np.mean(bid_arr[:, 1])
        avg_ask = np.mean(ask_arr[:, 1])
        whale_bid = any(v > avg_bid * 10 for _, v in bid_arr)
        whale_ask = any(v > avg_ask * 10 for _, v in ask_arr)

        best_bid = bid_arr[0, 0] if len(bid_arr) > 0 else 0
        best_ask = ask_arr[0, 0] if len(ask_arr) > 0 else 0
        spread   = (best_ask - best_bid) / best_ask if best_ask > 0 else 0

        return {
            "ob_signal": signal,
            "bid_ratio": bid_ratio,
            "bid_vol_idr": bid_vol_idr,
            "ask_vol_idr": ask_vol_idr,
            "whale_detected": whale_bid or whale_ask,
            "whale_buying": whale_bid,
            "whale_selling": whale_ask,
            "spread_pct": spread,
        }


    def analyze(
        self,
        df: pd.DataFrame,
        depth: Optional[dict] = None,
    ) -> IndicatorResult:
        if df is None or len(df) < 30:
            return IndicatorResult()

        result = IndicatorResult()

        ema_d  = self.calc_ema_signals(df)
        rsi_d  = self.calc_rsi(df)
        macd_d = self.calc_macd(df)
        bb_d   = self.calc_bollinger_bands(df)
        vol_d  = self.calc_volume(df)
        tr_d   = self.calc_trend(df)
        sr_d   = self.calc_support_resistance(df)
        ob_d   = self.analyze_order_book(depth) if depth else {}

        result.ema_fast    = ema_d["ema_fast"]
        result.ema_slow    = ema_d["ema_slow"]
        result.ema_cross   = ema_d["ema_cross"]
        result.rsi         = rsi_d["rsi"]
        result.rsi_signal  = rsi_d["rsi_signal"]
        result.macd        = macd_d["macd"]
        result.macd_signal = macd_d["macd_signal"]
        result.macd_hist   = macd_d["macd_hist"]
        result.macd_direction = macd_d["macd_direction"]
        result.bb_upper    = bb_d["bb_upper"]
        result.bb_middle   = bb_d["bb_middle"]
        result.bb_lower    = bb_d["bb_lower"]
        result.bb_signal   = bb_d["bb_signal"]
        result.bb_width    = bb_d["bb_width"]
        result.volume_avg     = vol_d["volume_avg"]
        result.volume_current = vol_d["volume_current"]
        result.volume_spike   = vol_d["volume_spike"]
        result.trend       = tr_d["trend"]
        result.support     = sr_d["support"]
        result.resistance  = sr_d["resistance"]

        buy = 0
        sell = 0

        if ema_d["ema_bullish"]:
            buy += 1
        else:
            sell += 1
        if ema_d["ema_cross"] == "golden":
            buy += 1
        elif ema_d["ema_cross"] == "death":
            sell += 1

        if rsi_d["rsi_signal"] == "oversold":
            buy += 2
        elif rsi_d["rsi_signal"] == "overbought":
            sell += 2
        elif rsi_d["rsi"] < 50:
            buy += 0.5
        else:
            sell += 0.5

        if macd_d["macd_crossover"]:
            buy += 2
        elif macd_d["macd_crossunder"]:
            sell += 2
        elif macd_d["macd_direction"] == "bullish":
            buy += 1
        elif macd_d["macd_direction"] == "bearish":
            sell += 1

        if bb_d["bb_signal"] == "lower_touch":
            buy += 2
        elif bb_d["bb_signal"] == "upper_touch":
            sell += 2
        elif bb_d["bb_signal"] == "near_lower":
            buy += 1
        elif bb_d["bb_signal"] == "near_upper":
            sell += 1

        if vol_d["volume_spike"] and vol_d["volume_confirms_trend"]:
            if tr_d["trend"] == "uptrend":
                buy += 1
            elif tr_d["trend"] == "downtrend":
                sell += 1

        if ob_d.get("ob_signal") == "bid_dominant":
            buy += 1
        elif ob_d.get("ob_signal") == "ask_dominant":
            sell += 1
        if ob_d.get("whale_buying"):
            buy += 0.5
        if ob_d.get("whale_selling"):
            sell += 0.5

        result.buy_score  = int(buy)
        result.sell_score = int(sell)

        return result
