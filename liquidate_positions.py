
import sys
import os
import time

# Ensure src is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from src.config import load_settings
from src.trading import get_client, get_positions
from py_clob_client_v2.clob_types import OrderArgs, PartialCreateOrderOptions, OrderType
from py_clob_client_v2.order_builder.constants import SELL

def liquidate():
    print("🧹 LIQUIDATING ALL POSITIONS (MARKET SELL)...")
    
    settings = load_settings()
    client = get_client(settings)
    
    try:
        # 1. Fetch Positions
        positions = get_positions(settings)
        if not positions:
            print("✅ Portfolio is clean. Nothing to liquidate.")
            return

        print(f"Found {len(positions)} assets to sell.")
        
        # 2. Iterate and Sell
        for token_id, data in positions.items():
            size = float(data.get("size", 0))
            if size < 0.1: 
                continue
                
            print(f"\n🔻 Liquidating Token {str(token_id)[:15]}... (Size: {size})")
            
            # Try neg_risk=False first (standard), then True
            success = False
            for try_neg_risk in [False, True]:
                if success: break
                
                try:
                    print(f"   Attempting Sell (Price: $0.01, neg_risk={try_neg_risk})...")
                    
                    order_args = OrderArgs(
                        token_id=str(token_id),
                        price=0.01, # Sell as cheap as possible (Market Sell simulation)
                        size=size,
                        side=SELL
                    )
                    options = PartialCreateOrderOptions(neg_risk=try_neg_risk)
                    
                    # Sign
                    signed_order = client.create_order(order_args, options)
                    
                    # Post (Use FAK/IOC to dump into bids, or GTC to sit there)
                    # GTC is safer to ensure it stays if not fully filled, but FAK gives immediate feedback
                    # Let's use GTC to ensure we really try to exit.
                    resp = client.post_order(signed_order, OrderType.GTC)
                    
                    if isinstance(resp, dict):
                        err = resp.get("errorMsg")
                        if err and "invalid signature" in str(err).lower():
                             print(f"   ❌ Invalid Sig with neg_risk={try_neg_risk}")
                             continue # Try next config
                        
                        if resp.get("orderID") or resp.get("success"):
                             print(f"   ✅ ORDER PLACED! ID: {resp.get('orderID')}")
                             success = True
                        else:
                             print(f"   ⚠️ Unexpected response: {resp}")
                    else:
                         # List response?
                         print(f"   Response: {resp}")
                         success = True
                         
                except Exception as e:
                    print(f"   ❌ Error (neg_risk={try_neg_risk}): {e}")
            
            if not success:
                print("   ❌ FAILED TO LIQUIDATE THIS TOKEN.")
                
        print("\n✅ Liquidation run complete.")
        print("   Please wait 10s and run check_positions.py again to verify.")
            
    except Exception as e:
        print(f"❌ Critical Error: {e}")

if __name__ == "__main__":
    liquidate()
