
import sys
import os
import time

# Ensure src is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from src.config import load_settings
from src.trading import get_client, get_positions

def check_positions():
    print("CHECKING PORTFOLIO FOR STUCK POSITIONS...")
    
    settings = load_settings()
    # client = get_client(settings) # Not needed directly if we use wrapper
    
    try:
        # Use our wrapper which handles the HTTP request manual fallback
        position_dict = get_positions(settings)
        
        # The wrapper returns a dict {token_id: {size: ..., avg_price: ...}}
        # We need to adapt the display logic slightly
        
        print(f"\nFound {len(position_dict)} positions:")
        print("-" * 60)
        print(f"{'Token ID':<15} | {'Size':<10} | {'Avg Price':<10}")
        print("-" * 60)
        
        has_positions = False
        for token_id, data in position_dict.items():
            size = data.get("size", 0)
            if size < 0.1: continue # Skip dust
            
            has_positions = True
            avg_price = data.get("avg_price", 0)
                
            print(f"{str(token_id)[:15]} | {size:<10.2f} | {avg_price:<10.4f}")
            
        print("-" * 60)
        
        if has_positions:
            print("\nWARNING: You have open positions.")
            print("   If these are from a crashed bot, they might be UNHEDGED.")
            print("   Please check Polymarket website execution history.")
        else:
            print("\n✅ Portfolio is clean (no significant positions).")
            
    except Exception as e:
        print(f"❌ Error fetching positions: {e}")

if __name__ == "__main__":
    check_positions()
