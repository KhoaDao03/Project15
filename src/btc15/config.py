import os
from dataclasses import dataclass, field

from .strategies.settlement_edge.config import Strategy as Strategy


@dataclass(frozen=True)
class Settings:
    asset: str = "BTC"
    mode: str = "PAPER"
    enable_live: bool = False
    database_url: str = "sqlite:///data/btc15.db"
    data_dir: str = "data"
    api_key_id: str = ""
    private_key_path: str = ""
    pyth_pro_api_key: str = field(default="", repr=False)
    rest_url: str = "https://external-api.kalshi.com/trade-api/v2"
    ws_url: str = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"

    @classmethod
    def env(cls):
        from dotenv import load_dotenv

        load_dotenv()
        return cls(
            mode=os.getenv("TRADING_MODE", "PAPER").upper(),
            enable_live=os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true",
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            data_dir=os.getenv("DATA_DIR", "data"),
            api_key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", ""),
            pyth_pro_api_key=os.getenv("PYTH_PRO_API_KEY", ""),
        )

    def guard(self):
        if self.mode not in ("PAPER", "BACKTEST", "LIVE"):
            raise ValueError("Unknown mode")
        if self.mode == "LIVE" or self.enable_live:
            raise RuntimeError("LIVE DISABLED: production submission and paper validation are not complete")
