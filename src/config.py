import os
from dataclasses import dataclass
from typing import List

from dotenv import load_dotenv, dotenv_values

# Load root .env file as fallback/global defaults
load_dotenv(override=False)


@dataclass
class Settings:
    api_key: str = os.getenv("POLYMARKET_API_KEY", "")
    api_secret: str = os.getenv("POLYMARKET_API_SECRET", "")
    api_passphrase: str = os.getenv("POLYMARKET_API_PASSPHRASE", "")
    private_key: str = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    signature_type: int = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1"))
    funder: str = os.getenv("POLYMARKET_FUNDER", "")
    market_slug: str = os.getenv("POLYMARKET_MARKET_SLUG", "")
    market_id: str = os.getenv("POLYMARKET_MARKET_ID", "")
    yes_token_id: str = os.getenv("POLYMARKET_YES_TOKEN_ID", "")
    no_token_id: str = os.getenv("POLYMARKET_NO_TOKEN_ID", "")
    ws_url: str = os.getenv("POLYMARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com")
    use_wss: bool = os.getenv("USE_WSS", "false").lower() == "true"
    target_pair_cost: float = float(os.getenv("TARGET_PAIR_COST", "0.99"))
    balance_slack: float = float(os.getenv("BALANCE_SLACK", "0.15"))
    order_size: float = float(os.getenv("ORDER_SIZE", "50"))
    min_liquidity: float = float(os.getenv("MIN_LIQUIDITY", "5.0"))
    order_type: str = os.getenv("ORDER_TYPE", "FOK").upper()
    yes_buy_threshold: float = float(os.getenv("YES_BUY_THRESHOLD", "0.45"))
    no_buy_threshold: float = float(os.getenv("NO_BUY_THRESHOLD", "0.45"))
    verbose: bool = os.getenv("VERBOSE", "false").lower() == "true"
    dry_run: bool = os.getenv("DRY_RUN", "false").lower() == "true"
    cooldown_seconds: float = float(os.getenv("COOLDOWN_SECONDS", "10"))
    sim_balance: float = float(os.getenv("SIM_BALANCE", "0"))
    profile_wallet: str = os.getenv("POLYMARKET_PROFILE", "")
    strategy: int = int(os.getenv("STRATEGY", "1"))
    target_market: str = os.getenv("TARGET_MARKET", "BTC_15M").upper()
    relayer_api_key: str = os.getenv("RELAYER_API_KEY", "")
    relayer_api_key_address: str = os.getenv("RELAYER_API_KEY_ADDRESS", "")
    builder_code: str = os.getenv("POLY_BUILDER_CODE", "")


def load_settings() -> List[Settings]:
    """
    Load settings for all clients. 
    Scans the `clients/` directory for any .env files. If none are found, 
    falls back to using the root .env file (returning a single-client list).
    """
    clients_dir = "clients"
    settings_list = []
    
    if os.path.exists(clients_dir) and os.path.isdir(clients_dir):
        # Look for .env files in clients/
        for filename in os.listdir(clients_dir):
            if filename.endswith(".env"):
                filepath = os.path.join(clients_dir, filename)
                # Parse the env file specifically for this client
                client_vars = dotenv_values(filepath)
                
                # Helper to get value: prefer client's specific setting, fallback to root env
                def get_val(key, default=""):
                    return client_vars.get(key) if client_vars.get(key) is not None else os.getenv(key, default)
                
                s = Settings(
                    api_key=get_val("POLYMARKET_API_KEY", ""),
                    api_secret=get_val("POLYMARKET_API_SECRET", ""),
                    api_passphrase=get_val("POLYMARKET_API_PASSPHRASE", ""),
                    private_key=get_val("POLYMARKET_PRIVATE_KEY", ""),
                    signature_type=int(get_val("POLYMARKET_SIGNATURE_TYPE", "1")),
                    funder=get_val("POLYMARKET_FUNDER", ""),
                    market_slug=get_val("POLYMARKET_MARKET_SLUG", ""),
                    market_id=get_val("POLYMARKET_MARKET_ID", ""),
                    yes_token_id=get_val("POLYMARKET_YES_TOKEN_ID", ""),
                    no_token_id=get_val("POLYMARKET_NO_TOKEN_ID", ""),
                    ws_url=get_val("POLYMARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com"),
                    use_wss=get_val("USE_WSS", "false").lower() == "true",
                    target_pair_cost=float(get_val("TARGET_PAIR_COST", "0.99")),
                    balance_slack=float(get_val("BALANCE_SLACK", "0.15")),
                    order_size=float(get_val("ORDER_SIZE", "50")),
                    min_liquidity=float(get_val("MIN_LIQUIDITY", "5.0")),
                    order_type=get_val("ORDER_TYPE", "FOK").upper(),
                    yes_buy_threshold=float(get_val("YES_BUY_THRESHOLD", "0.45")),
                    no_buy_threshold=float(get_val("NO_BUY_THRESHOLD", "0.45")),
                    verbose=get_val("VERBOSE", "false").lower() == "true",
                    dry_run=get_val("DRY_RUN", "false").lower() == "true",
                    cooldown_seconds=float(get_val("COOLDOWN_SECONDS", "10")),
                    sim_balance=float(get_val("SIM_BALANCE", "0")),
                    profile_wallet=get_val("POLYMARKET_PROFILE", ""),
                    strategy=int(get_val("STRATEGY", "1")),
                    target_market=get_val("TARGET_MARKET", "BTC_15M").upper(),
                    relayer_api_key=get_val("RELAYER_API_KEY", ""),
                    relayer_api_key_address=get_val("RELAYER_API_KEY_ADDRESS", ""),
                    builder_code=get_val("POLY_BUILDER_CODE", ""),
                )
                
                # Only add client if they have a private key
                if s.private_key:
                    settings_list.append(s)

    # Fallback to single root .env client if no client configs are found
    if not settings_list:
        s = Settings()
        if s.private_key:
            settings_list.append(s)
            
    return settings_list
