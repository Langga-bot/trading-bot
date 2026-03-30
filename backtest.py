import logging
import json
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv  

load_dotenv()

import pandas as pd
import numpy as np

from indicators import TechnicalIndicators
from strategy import StrategyEngine, Signal
from config import BACKTEST_CONFIG, RISK_CONFIG, STRATEGY_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    pair: str
    entry_price: float
    exit_price: float
    entry_index: int
    exit_index: int
    coin_amount: float
    idr_invested: float
    idr_received: float
    pnl_idr: float
    pnl_pct: float
    exit_reason: str
    duration_candles: int


@dataclass
class BacktestResult:
    pair: str
    initial_capital: float
    final_capital: float
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    winrate: float = 0.0
    total_profit_idr: float = 0.0
    total_loss_idr: float = 0.0
    net_pnl_idr: float = 0.0
    net_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_profit_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    avg_duration_candles: float = 0.0
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)


class Backtester:
    def __init__(self):
        self.cfg      = BACKTEST_CONFIG
        self.risk_cfg = RISK_CONFIG
        self.ind      = TechnicalIndicators()


    @staticmethod
    def load_csv(filepath: str) -> pd.DataFrame:
        df = pd.read_csv(filepath)
        df.columns = [c.lower() for c in df.columns]
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.set_index("timestamp", inplace=True)
        df = df.astype({"open": float, "high": float, "low": float,
                         "close": float, "volume": float})
        df.sort_index(inplace=True)
        logger.info(f"[BACKTEST] Loaded {len(df)} candles from {filepath}")
        return df

    def generate_sample_data(self, pair: str = "btc_idr", n: int = 500) -> pd.DataFrame:
        np.random.seed(42)
        dates   = pd.date_range("2024-01-01", periods=n, freq="5min")
        price   = 700_000_000.0
        closes  = [price]

        for _ in range(n - 1):
            ret = np.random.normal(0, 0.002)
            price = closes[-1] * (1 + ret)
            closes.append(max(price, 1))

        closes = np.array(closes)
        highs  = closes * (1 + np.abs(np.random.normal(0, 0.001, n)))
        lows   = closes * (1 - np.abs(np.random.normal(0, 0.001, n)))
        opens  = np.roll(closes, 1)
        opens[0] = closes[0]
        vols   = np.random.exponential(1_000_000, n)

        df = pd.DataFrame({
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": vols,
        }, index=dates)
        return df

    def run(self, df: pd.DataFrame, pair: str = "btc_idr") -> BacktestResult:
        result = BacktestResult(
            pair=pair,
            initial_capital=self.cfg["initial_capital_idr"],
            final_capital=self.cfg["initial_capital_idr"],
        )

        capital    = self.cfg["initial_capital_idr"]
        commission = self.cfg["commission_pct"]
        warmup     = 35
        trades     = []
        equity     = [capital]

        # State posisi
        in_position    = False
        entry_price    = 0.0
        coin_amount    = 0.0
        idr_invested   = 0.0
        stop_loss_p    = 0.0
        take_profit_p  = 0.0
        trailing_high  = 0.0
        trailing_stop  = 0.0
        entry_idx      = 0
        strategy_name  = ""

        logger.info(f"[BACKTEST] Mulai backtest {pair} | {len(df)} candles")

        for i in range(warmup, len(df)):
            candles_slice = df.iloc[max(0, i-100):i]
            candle_list   = candles_slice.reset_index().rename(
                columns={"index": "timestamp"}
            ).to_dict("records")

            for c in candle_list:
                if hasattr(c["timestamp"], "timestamp"):
                    c["timestamp"] = int(c["timestamp"].timestamp())

            current_price = float(df["close"].iloc[i])
            current_high  = float(df["high"].iloc[i])
            current_low   = float(df["low"].iloc[i])

            if in_position:
                exit_reason = None

                if current_low <= stop_loss_p:
                    exit_price_act = stop_loss_p
                    exit_reason    = "STOP_LOSS"

                elif current_high >= take_profit_p:
                    exit_price_act = take_profit_p
                    exit_reason    = "TAKE_PROFIT"

                else:
                    if current_high > trailing_high:
                        trailing_high = current_high
                        trailing_stop = trailing_high * (1 - self.risk_cfg["trailing_stop_pct"])
                    if current_low <= trailing_stop:
                        exit_price_act = trailing_stop
                        exit_reason    = "TRAILING_STOP"

                if exit_reason:
                    idr_received = coin_amount * exit_price_act * (1 - commission)
                    pnl_idr      = idr_received - idr_invested
                    pnl_pct      = pnl_idr / idr_invested * 100
                    capital     += idr_received

                    trades.append(BacktestTrade(
                        pair=pair,
                        entry_price=entry_price,
                        exit_price=exit_price_act,
                        entry_index=entry_idx,
                        exit_index=i,
                        coin_amount=coin_amount,
                        idr_invested=idr_invested,
                        idr_received=idr_received,
                        pnl_idr=pnl_idr,
                        pnl_pct=pnl_pct,
                        exit_reason=exit_reason,
                        duration_candles=i - entry_idx,
                    ))
                    in_position = False
                    equity.append(capital)
                    continue

            if not in_position and capital > self.risk_cfg["min_order_idr"]:
                try:
                    ind = self.ind.analyze(
                        self.ind.prepare_dataframe(candle_list)
                    )
                    min_score = STRATEGY_CONFIG["min_buy_score"]

                    if ind.buy_score >= min_score and ind.sell_score < ind.buy_score:
                        trade_size   = capital * self.risk_cfg["trade_size_pct"]
                        cost         = trade_size * (1 + commission)
                        if cost > capital:
                            continue

                        entry_price    = current_price
                        coin_amount    = trade_size / entry_price
                        idr_invested   = trade_size
                        capital       -= cost
                        stop_loss_p    = entry_price * (1 - self.risk_cfg["stop_loss_pct"])
                        take_profit_p  = entry_price * (1 + self.risk_cfg["take_profit_pct"])
                        trailing_high  = entry_price
                        trailing_stop  = stop_loss_p
                        entry_idx      = i
                        in_position    = True

                except Exception:
                    pass

            equity.append(capital)

        if in_position:
            last_price   = float(df["close"].iloc[-1])
            idr_received = coin_amount * last_price * (1 - commission)
            pnl_idr      = idr_received - idr_invested
            capital     += idr_received
            trades.append(BacktestTrade(
                pair=pair, entry_price=entry_price, exit_price=last_price,
                entry_index=entry_idx, exit_index=len(df)-1,
                coin_amount=coin_amount, idr_invested=idr_invested,
                idr_received=idr_received, pnl_idr=pnl_idr,
                pnl_pct=pnl_idr/idr_invested*100, exit_reason="END_OF_DATA",
                duration_candles=len(df)-1-entry_idx,
            ))

        return self._calc_stats(result, trades, equity, capital)

    def _calc_stats(
        self,
        result: BacktestResult,
        trades: List[BacktestTrade],
        equity: List[float],
        final_capital: float,
    ) -> BacktestResult:
        result.final_capital = final_capital
        result.trades        = trades
        result.equity_curve  = equity

        if not trades:
            logger.warning("[BACKTEST] Tidak ada trade yang terjadi!")
            return result

        result.total_trades   = len(trades)
        profits = [t for t in trades if t.pnl_idr >= 0]
        losses  = [t for t in trades if t.pnl_idr < 0]

        result.winning_trades = len(profits)
        result.losing_trades  = len(losses)
        result.winrate        = len(profits) / len(trades) * 100

        result.total_profit_idr = sum(t.pnl_idr for t in profits)
        result.total_loss_idr   = abs(sum(t.pnl_idr for t in losses))
        result.net_pnl_idr      = result.total_profit_idr - result.total_loss_idr
        result.net_pnl_pct      = result.net_pnl_idr / result.initial_capital * 100

        if profits:
            result.avg_profit_pct = np.mean([t.pnl_pct for t in profits])
        if losses:
            result.avg_loss_pct   = np.mean([t.pnl_pct for t in losses])

        result.profit_factor = (
            result.total_profit_idr / result.total_loss_idr
            if result.total_loss_idr > 0 else float("inf")
        )

        result.best_trade_pct  = max(t.pnl_pct for t in trades)
        result.worst_trade_pct = min(t.pnl_pct for t in trades)
        result.avg_duration_candles = np.mean([t.duration_candles for t in trades])

        eq = np.array(equity)
        running_max = np.maximum.accumulate(eq)
        drawdowns   = (eq - running_max) / running_max * 100
        result.max_drawdown_pct = abs(min(drawdowns))

        returns = np.diff(eq) / eq[:-1]
        if len(returns) > 1 and returns.std() != 0:
            result.sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(252 * 288)

        return result


    def print_report(self, result: BacktestResult) -> str:
        sep = "=" * 50
        report = f"""
{sep}
📊 LAPORAN BACKTESTING — {result.pair.upper()}
{sep}
💰 Modal Awal       : Rp {result.initial_capital:>15,.0f}
💰 Modal Akhir      : Rp {result.final_capital:>15,.0f}
📈 Net PnL          : Rp {result.net_pnl_idr:>+15,.0f} ({result.net_pnl_pct:+.2f}%)

📊 STATISTIK TRADE
─────────────────────────────────────────────────
🔄 Total Trade      : {result.total_trades:>5}
✅ Win Trade        : {result.winning_trades:>5}
❌ Loss Trade       : {result.losing_trades:>5}
🎯 Win Rate         : {result.winrate:>8.2f}%
⚖️  Profit Factor   : {result.profit_factor:>8.2f}

📈 PERFORMA
─────────────────────────────────────────────────
🟢 Avg Profit/Trade : {result.avg_profit_pct:>+7.2f}%
🔴 Avg Loss/Trade   : {result.avg_loss_pct:>+7.2f}%
🏆 Best Trade       : {result.best_trade_pct:>+7.2f}%
💥 Worst Trade      : {result.worst_trade_pct:>+7.2f}%
⏱️  Avg Duration    : {result.avg_duration_candles:>7.1f} candles

⚠️  RISIKO
─────────────────────────────────────────────────
📉 Max Drawdown     : {result.max_drawdown_pct:>7.2f}%
📐 Sharpe Ratio     : {result.sharpe_ratio:>7.2f}
{sep}
"""
        print(report)
        logger.info(
            f"[BACKTEST] Done: {result.total_trades} trades | "
            f"WR={result.winrate:.1f}% | "
            f"PnL={result.net_pnl_pct:+.2f}% | "
            f"DD={result.max_drawdown_pct:.2f}%"
        )
        return report

    def export_trades_csv(self, result: BacktestResult, filepath: str) -> None:
        rows = []
        for t in result.trades:
            rows.append({
                "pair": t.pair,
                "entry_price": t.entry_price,
                "exit_price":  t.exit_price,
                "coin_amount": t.coin_amount,
                "idr_invested": t.idr_invested,
                "pnl_idr": t.pnl_idr,
                "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason,
                "duration_candles": t.duration_candles,
            })
        pd.DataFrame(rows).to_csv(filepath, index=False)
        logger.info(f"[BACKTEST] Trades exported to {filepath}")
