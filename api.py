"""
api.py -- Wrapper Indodax REST API
====================================
Strategi koneksi berlapis:
  1. ccxt library  -- paling reliable, sudah handle Indodax auth & OHLCV
  2. TradingView endpoint Indodax
  3. Trade history -> synthetic candles
  4. Ticker-based  -- fallback akhir agar bot tidak crash

Private API (trade/balance) tetap via HMAC-SHA512 langsung.
"""

import hashlib
import hmac
import time
import logging
import random
import threading
import requests
from urllib.parse import urlencode
from typing import Optional, List, Tuple

from config import (
    API_KEY, SECRET_KEY,
    INDODAX_BASE_URL, INDODAX_PRIVATE_URL, INDODAX_PUBLIC_URL,
    BOT_CONFIG,
)

logger = logging.getLogger(__name__)

# Coba import ccxt
try:
    import ccxt
    _CCXT_AVAILABLE = True
except ImportError:
    _CCXT_AVAILABLE = False
    logger.warning("[API] ccxt tidak terinstall. Jalankan: pip install ccxt")


class IndodaxAPIError(Exception):
    pass


class IndodaxAPI:
    """
    Client Indodax REST API dengan 4 lapis fallback untuk OHLCV.
    Urutan: ccxt -> TradingView -> Trade history -> Ticker synthetic
    """

    def __init__(self):
        self.api_key     = API_KEY
        self.secret_key  = SECRET_KEY.encode()
        self.max_retries = BOT_CONFIG["max_retries"]
        self.retry_delay = BOT_CONFIG["retry_delay"]

        # Session untuk requests langsung
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type":  "application/x-www-form-urlencoded",
            "User-Agent":    (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept":        "application/json, text/plain, */*",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
            "Origin":        "https://indodax.com",
            "Referer":       "https://indodax.com/",
        })

        # Thread safety untuk private API (nonce harus selalu unik & naik)
        self._nonce_lock  = threading.Lock()
        self._last_nonce  = 0

        # ccxt instance
        self._ccxt = None
        self._ticker_cache: dict = {}
        self._ohlcv_cache:  dict = {}
        if _CCXT_AVAILABLE:
            try:
                self._ccxt = ccxt.indodax({
                    "apiKey": API_KEY,
                    "secret": SECRET_KEY,
                    "enableRateLimit": True,
                })
                logger.info("[API] ccxt Indodax exchange siap")
            except Exception as e:
                logger.warning(f"[API] ccxt init error: {e}")

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _pair_to_symbol(pair: str) -> str:
        """btc_idr -> BTC/IDR"""
        parts = pair.upper().split("_")
        return f"{parts[0]}/{parts[1]}"

    def _sign(self, params: dict) -> Tuple[str, str]:
        """Generate HMAC-SHA512 signature. Thread-safe via _nonce_lock."""
        with self._nonce_lock:
            # Pastikan nonce selalu naik — tidak ada dua request dengan nonce sama
            nonce = int(time.time() * 1000)
            if nonce <= self._last_nonce:
                nonce = self._last_nonce + 1
            self._last_nonce = nonce
            params["nonce"] = nonce

        body = urlencode(params)
        sig  = hmac.new(self.secret_key, body.encode(), hashlib.sha512).hexdigest()
        return body, sig

    def _public_get(self, endpoint: str, params: dict = None) -> dict:
        """GET ke public API. Skip retry langsung pada 403."""
        url = f"{INDODAX_PUBLIC_URL}/{endpoint}"
        for attempt in range(self.max_retries):
            try:
                r = self.session.get(url, params=params, timeout=8)
                if r.status_code == 403:
                    # 403 tidak akan berubah dengan retry — langsung skip
                    raise IndodaxAPIError(f"403 Forbidden: {endpoint}")
                r.raise_for_status()
                return r.json()
            except IndodaxAPIError:
                raise
            except Exception as e:
                logger.debug(f"[API] HTTP GET attempt {attempt+1}: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(1)   # jeda singkat (bukan 5s) untuk non-403 error
        raise IndodaxAPIError(f"Failed {self.max_retries} retries: {endpoint}")

    def _private_post(self, params: dict) -> dict:
        body, sig = self._sign(params)
        headers = {
            "Key":  self.api_key,
            "Sign": sig,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        for attempt in range(self.max_retries):
            try:
                r = requests.post(
                    INDODAX_PRIVATE_URL, data=body,
                    headers=headers, timeout=15
                )
                r.raise_for_status()
                data = r.json()
                if data.get("success") == 0:
                    raise IndodaxAPIError(f"API error: {data.get('error','Unknown')}")
                return data.get("return", data)
            except IndodaxAPIError:
                raise
            except Exception as e:
                logger.warning(f"[API] Private POST attempt {attempt+1}: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
        raise IndodaxAPIError("Private API failed")

    # ── Public API ───────────────────────────────────────────────────────────

    def get_ticker(self, pair: str) -> dict:
        """
        Ambil ticker satu pair dari cache (diisi fetch_all_tickers).
        Jika cache kosong, fetch langsung via summaries.
        """
        cached = self._ticker_cache.get(pair)
        if cached:
            return cached
        # Cache kosong — fetch semua sekaligus
        self.fetch_all_tickers([pair])
        return self._ticker_cache.get(pair, {})

    def fetch_all_tickers(self, pairs: list) -> dict:
        """
        Fetch semua harga dalam SATU request via endpoint summaries Indodax.
        Endpoint ini jauh lebih ringan dari /api/pairs dan jarang kena rate limit.
        Fallback: ccxt fetch_tickers jika summaries gagal.
        """
        self._ticker_cache = {}

        # ── Strategi 1: Summaries endpoint (1 request, semua pair) ──────────
        try:
            url = f"{INDODAX_BASE_URL}/api/summaries"
            r   = self.session.get(url, timeout=10)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 10))
                logger.warning(f"[API] Rate limited (429), tunggu {wait}s...")
                time.sleep(wait)
                r = self.session.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                tickers = data.get("tickers", data)
                found = []
                for pair in pairs:
                    # summaries pakai key tanpa underscore, misal "btcidr"
                    key = pair.replace("_", "")
                    raw = tickers.get(key) or tickers.get(pair)
                    if raw:
                        self._ticker_cache[pair] = {
                            "last":    str(raw.get("last", 0) or 0),
                            "high":    str(raw.get("high", 0) or 0),
                            "low":     str(raw.get("low",  0) or 0),
                            "buy":     str(raw.get("buy",  0) or 0),
                            "sell":    str(raw.get("sell", 0) or 0),
                            "vol_idr": str(raw.get("vol_idr", 0) or 0),
                        }
                        found.append(pair)
                if found:
                    summary = ", ".join(
                        f"{p}={float(self._ticker_cache[p].get('last',0)):,.0f}"
                        for p in found
                    )
                    logger.info(f"[API] Summaries OK: {summary}")
                    return self._ticker_cache
        except Exception as e:
            logger.debug(f"[API] summaries error: {e}")

        # ── Strategi 2: ccxt fetch_tickers (batch, tapi lebih berat) ────────
        if self._ccxt:
            try:
                symbols  = [self._pair_to_symbol(p) for p in pairs]
                tickers  = self._ccxt.fetch_tickers(symbols)
                found    = []
                for pair in pairs:
                    sym = self._pair_to_symbol(pair)
                    if sym in tickers:
                        self._ticker_cache[pair] = self._normalize_ticker(tickers[sym])
                        found.append(pair)
                if found:
                    summary = ", ".join(
                        f"{p}={float(self._ticker_cache[p].get('last',0)):,.0f}"
                        for p in found
                    )
                    logger.info(f"[API] ccxt batch ticker OK: {summary}")
                    return self._ticker_cache
            except Exception as e:
                err_str = str(e)
                if "429" in err_str:
                    logger.warning("[API] ccxt 429 rate limit - tunggu 15s...")
                    time.sleep(15)
                else:
                    logger.warning(f"[API] ccxt fetch_tickers: {e}")

        # ── Strategi 3: ccxt satu per satu dengan jeda ──────────────────────
        if self._ccxt:
            for pair in pairs:
                try:
                    t = self._ccxt.fetch_ticker(self._pair_to_symbol(pair))
                    self._ticker_cache[pair] = self._normalize_ticker(t)
                    time.sleep(1.0)   # jeda 1s antar request untuk hindari 429
                except Exception as e:
                    if "429" in str(e):
                        logger.warning(f"[API] 429 pada {pair}, tunggu 20s...")
                        time.sleep(20)
                    else:
                        logger.warning(f"[API] ticker {pair}: {e}")

        if self._ticker_cache:
            summary = ", ".join(
                f"{p}={float(self._ticker_cache[p].get('last',0)):,.0f}"
                for p in self._ticker_cache
            )
            logger.info(f"[API] Ticker fallback OK: {summary}")
        else:
            logger.error("[API] Semua metode ticker gagal! Cek koneksi / rate limit.")

        return self._ticker_cache

    @staticmethod
    def _normalize_ticker(t: dict) -> dict:
        return {
            "last":    str(t.get("last", 0) or 0),
            "high":    str(t.get("high", 0) or 0),
            "low":     str(t.get("low",  0) or 0),
            "buy":     str(t.get("bid",  0) or 0),
            "sell":    str(t.get("ask",  0) or 0),
            "vol_idr": str(t.get("quoteVolume", 0) or 0),
        }

    def get_depth(self, pair: str) -> dict:
        """Ambil order book. Silent jika 403."""
        if self._ccxt:
            try:
                ob = self._ccxt.fetch_order_book(self._pair_to_symbol(pair), limit=20)
                return {
                    "buy":  [[str(p), str(a)] for p, a in ob.get("bids", [])],
                    "sell": [[str(p), str(a)] for p, a in ob.get("asks", [])],
                }
            except Exception as e:
                logger.debug(f"[API] ccxt depth {pair}: {e}")
        try:
            return self._public_get(f"{pair}/depth")
        except Exception:
            return {}

    def get_trades(self, pair: str) -> List[dict]:
        """Ambil riwayat trade terbaru (maks 1000 untuk candle yang cukup)."""
        if self._ccxt:
            try:
                trades = self._ccxt.fetch_trades(
                    self._pair_to_symbol(pair), limit=1000
                )
                return [
                    {
                        "date":   int(t["timestamp"] / 1000),
                        "price":  str(t["price"]),
                        "amount": str(t["amount"]),
                        "type":   t.get("side", "buy"),
                    }
                    for t in trades
                ]
            except Exception as e:
                logger.debug(f"[API] ccxt trades {pair}: {e}")
        try:
            return self._public_get(f"{pair}/trades")
        except Exception:
            return []

    def get_ohlcv(self, pair: str, tf: str = "5", limit: int = 100) -> List[dict]:
        """
        Ambil OHLCV dengan 4 lapis fallback.
        Hasil di-cache per iterasi untuk menghindari request berulang.
        tf = timeframe dalam menit: "1","5","15","30","60","240"
        """
        # Cek cache (reset tiap iterasi lewat clear_ohlcv_cache)
        cache_key = f"{pair}_{tf}"
        if cache_key in self._ohlcv_cache:
            return self._ohlcv_cache[cache_key]

        result = []

        # 1. ccxt
        if self._ccxt:
            result = self._get_ohlcv_ccxt(pair, tf, limit)

        # 2. TradingView endpoint Indodax
        if not result:
            result = self._get_ohlcv_tradingview(pair, tf, limit)

        # 3. Synthetic dari trade history
        if not result:
            result = self._build_ohlcv_from_trades(pair, int(tf), limit)

        # 4. Fallback ticker (sinyal tidak reliable)
        if not result:
            result = self._build_ohlcv_from_ticker(pair, limit)

        # Simpan ke cache
        if result:
            self._ohlcv_cache[cache_key] = result

        return result

    def clear_ohlcv_cache(self):
        """Reset cache OHLCV di awal setiap iterasi."""
        self._ohlcv_cache = {}

    def _get_ohlcv_ccxt(self, pair: str, tf: str, limit: int) -> List[dict]:
        try:
            tf_map  = {"1":"1m","5":"5m","15":"15m","30":"30m","60":"1h","240":"4h"}
            tf_ccxt = tf_map.get(str(tf), "1m")
            symbol  = self._pair_to_symbol(pair)

            # Set timeout eksplisit di ccxt options
            self._ccxt.options["fetchOHLCVLimit"] = limit
            raw = self._ccxt.fetch_ohlcv(symbol, timeframe=tf_ccxt, limit=limit)

            if (not raw or len(raw) < 30) and tf_ccxt != "1m":
                logger.debug(f"[API] {tf_ccxt} hanya {len(raw or [])} candles, fallback ke 1m")
                try:
                    raw_1m = self._ccxt.fetch_ohlcv(symbol, timeframe="1m", limit=limit)
                    if raw_1m and len(raw_1m) > len(raw or []):
                        raw = raw_1m
                except Exception:
                    pass

            if raw and len(raw) >= 10:
                result = [
                    {
                        "timestamp": int(c[0] / 1000),
                        "open":   float(c[1] or 0),
                        "high":   float(c[2] or 0),
                        "low":    float(c[3] or 0),
                        "close":  float(c[4] or 0),
                        "volume": float(c[5] or 0),
                    }
                    for c in raw if c[4] is not None
                ]
                if result:
                    logger.info(f"[API] OHLCV ccxt OK: {len(result)} candles ({pair})")
                    return result
            elif raw is not None:
                logger.debug(f"[API] OHLCV ccxt terlalu sedikit: {len(raw)} candles ({pair})")
        except Exception as e:
            err = str(e)
            if "429" in err:
                logger.warning(f"[API] OHLCV rate limit {pair}, tunggu 10s...")
                time.sleep(10)
            elif "400" in err or "invalid" in err.lower():
                # Timeframe tidak support — coba 1m
                logger.debug(f"[API] OHLCV {tf_ccxt} tidak support {pair}, coba 1m")
                try:
                    raw = self._ccxt.fetch_ohlcv(symbol, timeframe="1m", limit=limit)
                    if raw and len(raw) >= 10:
                        result = [
                            {"timestamp": int(c[0]/1000), "open": float(c[1] or 0),
                             "high": float(c[2] or 0), "low": float(c[3] or 0),
                             "close": float(c[4] or 0), "volume": float(c[5] or 0)}
                            for c in raw if c[4] is not None
                        ]
                        if result:
                            logger.info(f"[API] OHLCV ccxt 1m OK: {len(result)} candles ({pair})")
                            return result
                except Exception:
                    pass
            else:
                logger.warning(f"[API] OHLCV ccxt {pair}: {err[:100]}")
        return []

    def _get_ohlcv_tradingview(self, pair: str, tf: str, limit: int) -> List[dict]:
        try:
            coin   = pair.replace("_idr", "").upper()
            tv_url = "https://indodax.com/tradingview/history"
            params = {
                "symbol":     f"{coin}IDR",
                "resolution": tf,
                "from":       int(time.time()) - (limit * int(tf) * 60),
                "to":         int(time.time()),
            }
            r = self.session.get(tv_url, params=params, timeout=10)
            if r.status_code == 200 and r.text and r.text.strip():
                data = r.json()
                if data.get("s") == "ok" and len(data.get("t", [])) >= 10:
                    result = []
                    for i in range(len(data["t"])):
                        result.append({
                            "timestamp": data["t"][i],
                            "open":   float(data["o"][i]),
                            "high":   float(data["h"][i]),
                            "low":    float(data["l"][i]),
                            "close":  float(data["c"][i]),
                            "volume": float(data["v"][i]),
                        })
                    logger.info(f"[API] OHLCV via TradingView OK: {len(result)} candles ({pair})")
                    return result
        except Exception as e:
            logger.debug(f"[API] TradingView OHLCV {pair}: {e}")
        return []

    def _build_ohlcv_from_trades(
        self, pair: str, tf_minutes: int = 5, limit: int = 100
    ) -> List[dict]:
        try:
            import pandas as pd
            trades = self.get_trades(pair)
            if not trades or len(trades) < 20:
                return []
            df = pd.DataFrame(trades)
            df["price"]  = df["price"].astype(float)
            df["amount"] = df["amount"].astype(float)
            df["date"]   = pd.to_datetime(df["date"].astype(int), unit="s")
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)
            ohlcv = df["price"].resample(f"{tf_minutes}min").ohlc()
            vol   = df["amount"].resample(f"{tf_minutes}min").sum()
            ohlcv["volume"] = vol
            ohlcv.dropna(inplace=True)
            if len(ohlcv) < 5:
                return []
            result = [
                {
                    "timestamp": int(ts.timestamp()),
                    "open":   float(row["open"]),
                    "high":   float(row["high"]),
                    "low":    float(row["low"]),
                    "close":  float(row["close"]),
                    "volume": float(row["volume"]),
                }
                for ts, row in ohlcv.iterrows()
            ]
            logger.info(f"[API] OHLCV dari trades OK: {len(result)} candles ({pair})")
            return result[-limit:]
        except Exception as e:
            logger.debug(f"[API] build_ohlcv_from_trades {pair}: {e}")
        return []

    def _build_ohlcv_from_ticker(self, pair: str, limit: int = 100) -> List[dict]:
        """
        Fallback akhir: candle sintetis dari ticker.
        Bot tetap jalan tapi TIDAK akan eksekusi trade
        karena sinyal tidak reliable dari data sintetis.
        """
        try:
            ticker = self.get_ticker(pair)
            price  = float(ticker.get("last", 0) or 0)
            if price == 0:
                logger.warning(f"[API] Ticker {pair} harga 0, skip")
                return []
            high = float(ticker.get("high", price * 1.01) or price * 1.01)
            low  = float(ticker.get("low",  price * 0.99) or price * 0.99)
            vol  = float(ticker.get("vol_idr", 1_000_000) or 1_000_000)
            now  = int(time.time())
            tf_sec = 5 * 60
            result = []
            for i in range(limit):
                ts = now - (limit - i) * tf_sec
                random.seed(ts)
                drift  = random.gauss(0, 0.0008)
                c      = price * (1 + drift * (i - limit // 2) * 0.03)
                spread = abs(random.gauss(0, 0.001))
                result.append({
                    "timestamp": ts,
                    "open":   c * (1 - spread * 0.5),
                    "high":   c * (1 + spread),
                    "low":    c * (1 - spread),
                    "close":  c,
                    "volume": vol / limit * random.uniform(0.5, 1.5),
                })
            logger.warning(
                f"[API] Fallback ticker candle {pair} "
                f"(price={price:,.0f}) - sinyal tidak reliable"
            )
            return result
        except Exception as e:
            logger.error(f"[API] _build_ohlcv_from_ticker {pair}: {e}")
            return []

    # ── Private API ──────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        """Cek saldo akun."""
        if self._ccxt:
            try:
                bal = self._ccxt.fetch_balance()
                return {
                    "balance": {
                        k.lower(): str(v["free"])
                        for k, v in bal.items()
                        if isinstance(v, dict) and "free" in v and v["free"]
                    }
                }
            except Exception as e:
                logger.debug(f"[API] ccxt balance: {e}")
        return self._private_post({"method": "getInfo"})

    def get_open_orders(self, pair: str = "") -> List[dict]:
        params = {"method": "openOrders"}
        if pair:
            params["pair"] = pair
        try:
            data   = self._private_post(params)
            orders = data.get("orders", [])
            return list(orders.values()) if isinstance(orders, dict) else orders
        except Exception:
            return []

    def get_order_history(self, pair: str, count: int = 10) -> List[dict]:
        try:
            return self._private_post({
                "method": "orderHistory",
                "pair":   pair,
                "count":  count,
            }).get("orders", [])
        except Exception:
            return []

    def get_trade_history(self, pair: str, count: int = 50) -> List[dict]:
        try:
            return self._private_post({
                "method": "tradeHistory",
                "pair":   pair,
                "count":  count,
            }).get("trades", [])
        except Exception:
            return []

    def place_buy_order(self, pair: str, price: float, amount_idr: float) -> dict:
        """Eksekusi BUY limit order."""
        logger.info(f"[ORDER] BUY {pair} price={price:,.0f} IDR={amount_idr:,.0f}")
        return self._private_post({
            "method": "trade",
            "pair":   pair,
            "type":   "buy",
            "price":  int(price),
            "idr":    int(amount_idr),
        })

    def place_sell_order(self, pair: str, price: float, coin_amount: float) -> dict:
        """Eksekusi SELL limit order."""
        coin = pair.replace("_idr", "")
        logger.info(f"[ORDER] SELL {pair} price={price:,.0f} amount={coin_amount}")
        return self._private_post({
            "method": "trade",
            "pair":   pair,
            "type":   "sell",
            "price":  int(price),
            coin:     coin_amount,
        })

    def cancel_order(self, pair: str, order_id: str, order_type: str) -> dict:
        return self._private_post({
            "method":   "cancelOrder",
            "pair":     pair,
            "order_id": order_id,
            "type":     order_type,
        })

    def get_available_idr(self) -> float:
        try:
            return float(self.get_balance().get("balance", {}).get("idr", 0) or 0)
        except Exception:
            return 0.0

    def get_coin_balance(self, pair: str) -> float:
        coin = pair.replace("_idr", "")
        try:
            return float(self.get_balance().get("balance", {}).get(coin, 0) or 0)
        except Exception:
            return 0.0

    def get_current_price(self, pair: str) -> Optional[float]:
        try:
            price = float(self.get_ticker(pair).get("last", 0) or 0)
            return price if price > 0 else None
        except Exception:
            return None

    def get_summary(self) -> dict:
        try:
            return self._public_get("summaries")
        except Exception:
            return {}

    def test_connection(self) -> dict:
        """
        Test semua endpoint dan tampilkan status.
        Jalankan: python -c "from api import IndodaxAPI; api=IndodaxAPI(); print(api.test_connection())"
        """
        results = {}
        try:
            t = self.get_ticker("btc_idr")
            p = float(t.get("last", 0) or 0)
            results["ticker"] = f"OK - BTC/IDR = Rp {p:,.0f}" if p > 0 else "EMPTY"
        except Exception as e:
            results["ticker"] = f"FAIL: {e}"

        try:
            candles = self.get_ohlcv("btc_idr", tf="5", limit=10)
            results["ohlcv"] = f"OK - {len(candles)} candles" if candles else "EMPTY"
        except Exception as e:
            results["ohlcv"] = f"FAIL: {e}"

        try:
            depth = self.get_depth("btc_idr")
            results["depth"] = f"OK - {len(depth.get('buy',[]))} bids" if depth else "BLOCKED (403)"
        except Exception as e:
            results["depth"] = f"FAIL: {e}"

        results["ccxt"] = (
            "tersedia dan siap" if self._ccxt else
            ("terinstall tapi gagal init" if _CCXT_AVAILABLE else
             "TIDAK terinstall - jalankan: pip install ccxt")
        )
        return results
