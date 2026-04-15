import os
from dataclasses import dataclass, field
from typing import List, Dict

API_KEY    = os.getenv("INDODAX_API_KEY", "YOUR_API_KEY_HERE")
SECRET_KEY = os.getenv("INDODAX_SECRET_KEY", "YOUR_SECRET_KEY_HERE")

INDODAX_BASE_URL    = "https://indodax.com"
INDODAX_PRIVATE_URL = "https://indodax.com/tapi"
INDODAX_PUBLIC_URL  = "https://indodax.com/api"

TRADING_PAIRS: List[str] = [
    "btc_idr",
    "sol_idr",
    "eth_idr",
    "xrp_idr",
    "doge_idr",
    "ada_idr",
    "pippin_idr",
    "bnb_idr",
    "xaut_idr",
    "gxc_idr",
    "moonpig_idr",
    "sto_idr",
    "br_idr",
    "ont_idr",
    "fartcoin_idr",
    "hype_idr",
    "paxg_idr",
    "myx_idr",
    "trx_idr",
    "jellyjelly_idr",
    "drx_idr",
]

STRATEGY_CONFIG = {
    "ema_fast": 7,
    "ema_slow":  25,
 
    "rsi_period":     14,
    "rsi_overbought": 70,
    "rsi_oversold":   30,
 
    "macd_fast":   12,
    "macd_slow":   26,
    "macd_signal":  9,
 
    "bb_period": 20,
    "bb_std":     2.0,
 
    "volume_spike_multiplier": 2.0,   
 
    # Skor minimum untuk eksekusi BUY/SELL
    "min_buy_score":  7.5,
    "min_sell_score": 7.5,
 
    "taker_fee_pct": 0.003,   
 
    "min_profit_to_sell_pct": 0.008, 
 
    "buy_cooldown_minutes": 15,
 
    # Konfirmasi multi-candle untuk BUY
    "signal_confirm_candles": 3,
 
    "min_trend_strength": 0.08, 
 
    "min_bb_width": 0.01,          
 
    # Batas RSI untuk konfirmasi BUY
    "max_rsi_for_buy":  65,
    "min_rsi_for_sell": 45,     
}

RISK_CONFIG = {
    "max_open_positions": 4,
    "trade_size_pct":     0.25,

    "initial_balance_idr": 0,

    "stop_loss_pct":      0.025,
    "take_profit_pct":    0.03,
    "trailing_stop_pct":  0.015,

    "max_daily_loss_idr": 500_000,
    "max_trades_per_day": 30,

    "min_order_idr":      10_000,
}

BOT_CONFIG = {

    "loop_interval": 60,
    "pair_delay": 1.0,
    "rate_limit_backoff": 30,
    "candle_limit": 100,
    "timeframe": "1m",

    "max_retries": 2,
    "retry_delay": 3,

    "dry_run": False,
}

TELEGRAM_CONFIG = {
    "enabled": True,
    "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
}

DATABASE_CONFIG = {
    "type":        "postgresql" if os.getenv("DATABASE_URL") else "sqlite",
    "sqlite_path": "data/trades.db",
    "pg_url":      os.getenv("DATABASE_URL", ""),
}

LOG_CONFIG = {
    "level": "INFO",
    "file": "logs/bot.log",
    "max_bytes": 10 * 1024 * 1024,
    "backup_count": 5,
}

BACKTEST_CONFIG = {
    "initial_capital_idr": 1_000_000,
    "commission_pct": 0.003,
    "end_date": "2024-12-31",
}
