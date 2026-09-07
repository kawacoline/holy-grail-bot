"""
Monkey-patch to fix Cloudflare blocking by spoofing browser User-Agent.

The py-clob-client-v2 library uses "py_clob_client" as User-Agent which
Cloudflare easily detects and blocks for POST requests.

This patch replaces it with a real Chrome browser User-Agent.

Supports both V1 and V2 SDK (patches whichever is installed).
"""

# Real Chrome User-Agent
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

def patched_overloadHeaders(method: str, headers: dict) -> dict:
    """Patched version with browser User-Agent."""
    if headers is None:
        headers = dict()
    
    # Use real browser User-Agent instead of "py_clob_client"
    headers["User-Agent"] = BROWSER_USER_AGENT
    headers["Accept"] = "*/*"
    headers["Connection"] = "keep-alive"
    headers["Content-Type"] = "application/json"
    
    # Additional browser-like headers
    headers["Accept-Language"] = "en-US,en;q=0.9"
    headers["sec-ch-ua"] = '"Google Chrome";v="131", "Not_A Brand";v="24", "Chromium";v="131"'
    headers["sec-ch-ua-mobile"] = "?0"
    headers["sec-ch-ua-platform"] = '"Windows"'
    
    if method == "GET":
        headers["Accept-Encoding"] = "gzip"
    
    return headers


# Patch V2 SDK (primary)
try:
    import py_clob_client_v2.http_helpers.helpers as helpers_v2
    # V2 uses _overload_headers (snake_case)
    helpers_v2._overload_headers = patched_overloadHeaders
    
    # CRITICAL FIX for "WinError 10035 A non-blocking socket operation could not be completed immediately"
    import httpx
    helpers_v2._http_client = httpx.Client(http2=False)
    
    print("[OK] Cloudflare bypass & HTTP/2 thread bug patch applied (V2 SDK)")
except ImportError:
    pass

# Patch V1 SDK (legacy fallback)
try:
    import py_clob_client.http_helpers.helpers as helpers_v1
    # V1 uses overloadHeaders (camelCase)
    helpers_v1.overloadHeaders = patched_overloadHeaders
    print("[OK] Cloudflare bypass patch applied (V1 SDK)")
except ImportError:
    pass
