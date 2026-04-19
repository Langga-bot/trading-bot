import numpy as np
import pandas as pd
from typing import List, Dict, Optional
from dataclasses import dataclass, field

from config import STRATEGY_CONFIG


@dataclass
class IndicatorResult:
    ema_fast:   float = 0.0
    ema_slow:   float = 0.0
    ema_50:     float = 0.0
    ema_200:    float = 0.0
    ema_cross:  str   = "neutral"
    trend:           str   = "sideways"
    trend_strength:  float = 0.0
    above_ema50:     bool  = False
    above_ema200:    bool  = False
    pullback_to_ema: bool  = False
    higher_high:     bool  = False
    lower_low:       bool  = False
    rsi:        float = 50.0
    rsi_signal: str   = "neutral"
    rsi_divergence_bull: bool = False
    rsi_divergence_bear: bool = False
    macd:           float = 0.0
    macd_signal:    float = 0.0
    macd_hist:      float = 0.0
    macd_direction: str   = "neutral"
    macd_crossover:  bool = False
    macd_crossunder: bool = False
    bb_upper:  float = 0.0
    bb_middle: float = 0.0
    bb_lower:  float = 0.0
    bb_signal: str   = "neutral"
    bb_width:  float = 0.0
    volume_avg:            float = 0.0
    volume_current:        float = 0.0
    volume_spike:          bool  = False
    volume_buy_pressure:   bool  = False
    volume_sell_pressure:  bool  = False
    support:          float = 0.0
    resistance:       float = 0.0
    near_support:     bool  = False
    near_resistance:  bool  = False
    distance_to_res:  float = 0.0
    pattern:       str  = "none"
    pattern_score: int  = 0
    buy_score:  int = 0
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
        df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
        df.sort_index(inplace=True)
        return df

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def calc_ema_signals(self, df: pd.DataFrame) -> Dict:
        fast = self.cfg["ema_fast"]
        slow = self.cfg["ema_slow"]

        ema_f   = self.ema(df["close"], fast)
        ema_s   = self.ema(df["close"], slow)
        ema_50  = self.ema(df["close"], 50)
        ema_200 = self.ema(df["close"], 200) if len(df) >= 200 else self.ema(df["close"], min(len(df)//2, 100))

        price  = float(df["close"].iloc[-1])
        cur_f  = float(ema_f.iloc[-1])
        cur_s  = float(ema_s.iloc[-1])
        cur_50 = float(ema_50.iloc[-1])
        cur_200 = float(ema_200.iloc[-1])
        prev_f = float(ema_f.iloc[-2])
        prev_s = float(ema_s.iloc[-2])

        if prev_f < prev_s and cur_f > cur_s:
            cross = "golden"
        elif prev_f > prev_s and cur_f < cur_s:
            cross = "death"
        else:
            cross = "above" if cur_f > cur_s else "below"

        pullback = (price > cur_s and abs(price - cur_s) / cur_s < 0.005)

        return {
            "ema_fast": cur_f, "ema_slow": cur_s,
            "ema_50": cur_50, "ema_200": cur_200,
            "ema_cross": cross, "ema_bullish": cur_f > cur_s,
            "above_ema50": price > cur_50,
            "above_ema200": price > cur_200,
            "pullback_to_ema": pullback,
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

        current = float(rsi.iloc[-1])
        ob = self.cfg["rsi_overbought"]
        os_ = self.cfg["rsi_oversold"]
        signal = "overbought" if current >= ob else ("oversold" if current <= os_ else "neutral")

        div_bull = div_bear = False
        if len(rsi) >= 10:
            ps = df["close"].iloc[-10:]
            rs2 = rsi.iloc[-10:]
            if ps.iloc[-1] < ps.iloc[0] and rs2.iloc[-1] > rs2.iloc[0] and current < 50:
                div_bull = True
            if ps.iloc[-1] > ps.iloc[0] and rs2.iloc[-1] < rs2.iloc[0] and current > 50:
                div_bear = True

        return {"rsi": current, "rsi_signal": signal,
                "rsi_series": rsi, "rsi_divergence_bull": div_bull, "rsi_divergence_bear": div_bear}

    def calc_macd(self, df: pd.DataFrame) -> Dict:
        fast = self.cfg["macd_fast"]
        slow = self.cfg["macd_slow"]
        signal = self.cfg["macd_signal"]
        ema_f = self.ema(df["close"], fast)
        ema_s = self.ema(df["close"], slow)
        macd  = ema_f - ema_s
        sig   = self.ema(macd, signal)
        hist  = macd - sig
        curr_hist = float(hist.iloc[-1])
        prev_hist = float(hist.iloc[-2])
        direction = ("bullish" if curr_hist > 0 and curr_hist > prev_hist else
                     "bearish" if curr_hist < 0 and curr_hist < prev_hist else "neutral")
        return {
            "macd": float(macd.iloc[-1]), "macd_signal": float(sig.iloc[-1]),
            "macd_hist": curr_hist, "macd_direction": direction,
            "macd_crossover": prev_hist < 0 < curr_hist,
            "macd_crossunder": prev_hist > 0 > curr_hist,
        }

    def calc_bollinger_bands(self, df: pd.DataFrame) -> Dict:
        period = self.cfg["bb_period"]
        std_dev = self.cfg["bb_std"]
        middle = df["close"].rolling(period).mean()
        std    = df["close"].rolling(period).std()
        upper  = middle + std_dev * std
        lower  = middle - std_dev * std
        price = float(df["close"].iloc[-1])
        u, m, l = float(upper.iloc[-1]), float(middle.iloc[-1]), float(lower.iloc[-1])
        width = (u - l) / m if m != 0 else 0
        pct_b = (price - l) / (u - l) if (u - l) != 0 else 0.5
        signal = ("upper_touch" if pct_b >= 1.0 else "lower_touch" if pct_b <= 0.0 else
                  "near_upper" if pct_b > 0.8 else "near_lower" if pct_b < 0.2 else "neutral")
        return {"bb_upper": u, "bb_middle": m, "bb_lower": l, "bb_width": width, "bb_signal": signal}

    def calc_volume(self, df: pd.DataFrame) -> Dict:
        mult = self.cfg["volume_spike_multiplier"]
        vol_cur = float(df["volume"].iloc[-1])
        vol_avg = float(df["volume"].rolling(20).mean().iloc[-1])
        spike   = vol_cur > vol_avg * mult
        close_c = float(df["close"].iloc[-1])
        open_c  = float(df["open"].iloc[-1])
        close_p = float(df["close"].iloc[-2])
        buy_pressure  = close_c > open_c and vol_cur > vol_avg * 1.2
        sell_pressure = close_c < open_c and vol_cur > vol_avg * 1.2
        return {
            "volume_current": vol_cur, "volume_avg": vol_avg,
            "volume_spike": spike, "volume_ratio": vol_cur / vol_avg if vol_avg > 0 else 1.0,
            "volume_buy_pressure": buy_pressure, "volume_sell_pressure": sell_pressure,
            "volume_confirms_trend": (close_c > close_p) == (vol_cur > vol_avg),
        }

    def calc_trend(self, df: pd.DataFrame) -> Dict:
        period = 20
        if len(df) < period:
            return {"trend": "sideways", "trend_strength": 0, "higher_high": False, "lower_low": False, "trend_slope": 0}
        recent = df.tail(period)
        closes = recent["close"].values
        highs  = recent["high"].values
        lows   = recent["low"].values
        slope  = np.polyfit(np.arange(len(closes)), closes, 1)[0]
        norm   = slope / closes.mean() * 100
        trend  = "uptrend" if norm > 0.05 else "downtrend" if norm < -0.05 else "sideways"
        mid = len(highs) // 2
        hh = float(highs[-mid:].max()) > float(highs[:mid].max())
        ll = float(lows[-mid:].min())  < float(lows[:mid].min())
        return {"trend": trend, "trend_strength": abs(norm), "trend_slope": norm,
                "higher_high": hh, "lower_low": ll}

    def calc_support_resistance(self, df: pd.DataFrame) -> Dict:
        recent = df.tail(60)
        price  = float(df["close"].iloc[-1])
        support    = float(recent["low"].rolling(20, center=True).min().dropna().iloc[-1])
        resistance = float(recent["high"].rolling(20, center=True).max().dropna().iloc[-1])
        near_sup = abs(price - support)    / price < 0.015
        near_res = abs(price - resistance) / price < 0.015
        dist_res = (resistance - price) / price if resistance > price else 0.0
        return {"support": support, "resistance": resistance,
                "near_support": near_sup, "near_resistance": near_res, "distance_to_res": dist_res}

    @staticmethod
    def calc_candlestick_pattern(df: pd.DataFrame) -> Dict:
        if len(df) < 2:
            return {"pattern": "none", "pattern_score": 0}
        o  = float(df["open"].iloc[-1]);  h  = float(df["high"].iloc[-1])
        l  = float(df["low"].iloc[-1]);   c  = float(df["close"].iloc[-1])
        o2 = float(df["open"].iloc[-2]);  c2 = float(df["close"].iloc[-2])
        body = abs(c - o); rng = h - l
        if rng == 0: return {"pattern": "none", "pattern_score": 0}
        body_pct   = body / rng
        lower_wick = (min(o,c) - l) / rng
        upper_wick = (h - max(o,c)) / rng
        if body_pct < 0.1:
            return {"pattern": "doji", "pattern_score": 0}
        if lower_wick > 0.55 and upper_wick < 0.15 and c >= o:
            return {"pattern": "hammer", "pattern_score": 1}
        if upper_wick > 0.55 and lower_wick < 0.15 and c <= o:
            return {"pattern": "shooting_star", "pattern_score": -1}
        if c > o and c2 < o2 and c > o2 and o < c2:
            return {"pattern": "engulfing_bull", "pattern_score": 2}
        if c < o and c2 > o2 and c < o2 and o > c2:
            return {"pattern": "engulfing_bear", "pattern_score": -2}
        return {"pattern": "none", "pattern_score": 0}

    @staticmethod
    def analyze_order_book(depth: dict) -> Dict:
        try:
            bids = depth.get("buy", [])
            asks = depth.get("sell", [])
            bid_arr = np.array([[float(p), float(v)] for p, v in bids[:20]])
            ask_arr = np.array([[float(p), float(v)] for p, v in asks[:20]])
            if len(bid_arr) == 0 or len(ask_arr) == 0:
                raise ValueError
            bid_vol = float(np.sum(bid_arr[:, 0] * bid_arr[:, 1]))
            ask_vol = float(np.sum(ask_arr[:, 0] * ask_arr[:, 1]))
            total   = bid_vol + ask_vol
            bid_ratio = bid_vol / total if total > 0 else 0.5
            signal = "bid_dominant" if bid_ratio > 0.65 else "ask_dominant" if bid_ratio < 0.35 else "balanced"
            avg_bid = float(np.mean(bid_arr[:, 1]))
            avg_ask = float(np.mean(ask_arr[:, 1]))
            whale_bid = any(v > avg_bid * 10 for _, v in bid_arr)
            whale_ask = any(v > avg_ask * 10 for _, v in ask_arr)
            return {"ob_signal": signal, "bid_ratio": bid_ratio,
                    "whale_detected": whale_bid or whale_ask,
                    "whale_buying": whale_bid, "whale_selling": whale_ask}
        except Exception:
            return {"ob_signal": "neutral", "whale_detected": False,
                    "bid_ratio": 0.5, "whale_buying": False, "whale_selling": False}

    def analyze(self, df: pd.DataFrame, depth: Optional[dict] = None) -> IndicatorResult:
        if df is None or len(df) < 30:
            return IndicatorResult()

        res = IndicatorResult()
        ema_d  = self.calc_ema_signals(df)
        rsi_d  = self.calc_rsi(df)
        macd_d = self.calc_macd(df)
        bb_d   = self.calc_bollinger_bands(df)
        vol_d  = self.calc_volume(df)
        tr_d   = self.calc_trend(df)
        sr_d   = self.calc_support_resistance(df)
        cs_d   = self.calc_candlestick_pattern(df)
        ob_d   = self.analyze_order_book(depth) if depth else {}

        res.ema_fast = ema_d["ema_fast"]; res.ema_slow = ema_d["ema_slow"]
        res.ema_50 = ema_d["ema_50"]; res.ema_200 = ema_d["ema_200"]
        res.ema_cross = ema_d["ema_cross"]
        res.above_ema50 = ema_d["above_ema50"]; res.above_ema200 = ema_d["above_ema200"]
        res.pullback_to_ema = ema_d["pullback_to_ema"]
        res.rsi = rsi_d["rsi"]; res.rsi_signal = rsi_d["rsi_signal"]
        res.rsi_divergence_bull = rsi_d["rsi_divergence_bull"]
        res.rsi_divergence_bear = rsi_d["rsi_divergence_bear"]
        res.macd = macd_d["macd"]; res.macd_signal = macd_d["macd_signal"]
        res.macd_hist = macd_d["macd_hist"]; res.macd_direction = macd_d["macd_direction"]
        res.macd_crossover = macd_d["macd_crossover"]; res.macd_crossunder = macd_d["macd_crossunder"]
        res.bb_upper = bb_d["bb_upper"]; res.bb_middle = bb_d["bb_middle"]
        res.bb_lower = bb_d["bb_lower"]; res.bb_signal = bb_d["bb_signal"]; res.bb_width = bb_d["bb_width"]
        res.volume_avg = vol_d["volume_avg"]; res.volume_current = vol_d["volume_current"]
        res.volume_spike = vol_d["volume_spike"]
        res.volume_buy_pressure = vol_d["volume_buy_pressure"]
        res.volume_sell_pressure = vol_d["volume_sell_pressure"]
        res.trend = tr_d["trend"]; res.trend_strength = tr_d["trend_strength"]
        res.higher_high = tr_d["higher_high"]; res.lower_low = tr_d["lower_low"]
        res.support = sr_d["support"]; res.resistance = sr_d["resistance"]
        res.near_support = sr_d["near_support"]; res.near_resistance = sr_d["near_resistance"]
        res.distance_to_res = sr_d["distance_to_res"]
        res.pattern = cs_d["pattern"]; res.pattern_score = cs_d["pattern_score"]

        # Composite Score — Formula High Accuracy
        buy = sell = 0.0

        # 1. Trend utama EMA50/200 (fondasi utama)
        buy  += 1.5 if ema_d["above_ema50"]  else 0
        sell += 1.5 if not ema_d["above_ema50"] else 0
        buy  += 1.5 if ema_d["above_ema200"] else 0
        sell += 1.5 if not ema_d["above_ema200"] else 0

        # 2. EMA fast/slow + pullback
        buy  += 1.0 if ema_d["pullback_to_ema"] else 0
        buy  += 1.0 if ema_d["ema_cross"] == "golden" else 0
        sell += 1.0 if ema_d["ema_cross"] == "death"  else 0

        # 3. RSI
        if rsi_d["rsi_signal"] == "oversold":   buy  += 2.0
        elif rsi_d["rsi_signal"] == "overbought": sell += 2.0
        elif rsi_d["rsi"] < 50: buy += 0.5
        else: sell += 0.5
        buy  += 1.0 if rsi_d["rsi_divergence_bull"] else 0
        sell += 1.0 if rsi_d["rsi_divergence_bear"] else 0

        # 4. MACD
        if macd_d["macd_crossover"]:               buy  += 2.0
        elif macd_d["macd_crossunder"]:            sell += 2.0
        elif macd_d["macd_direction"] == "bullish": buy  += 1.0
        elif macd_d["macd_direction"] == "bearish": sell += 1.0

        # 5. Volume konfirmasi (bukan spike, tapi tekanan beli)
        buy  += 1.0 if vol_d["volume_buy_pressure"]  else 0
        sell += 1.0 if vol_d["volume_sell_pressure"] else 0

        # 6. Support/Resistance
        buy  += 0.5 if sr_d["near_support"] else 0
        sell += 0.5 if sr_d["near_resistance"] else 0
        sell += 0.5 if 0 < sr_d["distance_to_res"] < 0.01 else 0

        # 7. Candlestick pattern
        buy  += cs_d["pattern_score"] * 0.5 if cs_d["pattern_score"] > 0 else 0
        sell += abs(cs_d["pattern_score"]) * 0.5 if cs_d["pattern_score"] < 0 else 0

        # 8. Market structure
        buy  += 0.5 if tr_d["higher_high"] else 0
        sell += 0.5 if tr_d["lower_low"]   else 0

        # 9. Order book
        buy  += 0.5 if ob_d.get("ob_signal") == "bid_dominant" else 0
        sell += 0.5 if ob_d.get("ob_signal") == "ask_dominant" else 0

        res.buy_score  = int(buy)
        res.sell_score = int(sell)
        return res
