"""
Proxy Configuration

Manages the list of proxies and selection strategy.
Currently using random selection from available working proxies.
"""

import random

# List of available proxies (loaded from PROXY_URL environment variable or empty)
# Format: http://user:pass@ip:port
import os
_env_proxy = os.getenv("PROXY_URL")
PROXIES = [_env_proxy] if _env_proxy else []

def get_proxy():
    """Return a random proxy from the list."""
    import os
    if os.getenv("USE_PROXY", "true").lower() == "false":
        return None
        
    if not PROXIES:
        return None
    return random.choice(PROXIES)

def get_all_proxies():
    """Return the full list of proxies."""
    return PROXIES
