
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class CircuitBreaker:
    """
    Safety mechanism to stop the bot if balance drops too fast.
    """
    def __init__(self, initial_balance: float, max_drawdown_pct: float = 0.15):
        self.initial_balance = initial_balance
        self.max_drawdown_pct = max_drawdown_pct
        self.is_tripped = False
        self.start_time = time.time()
        
        # If balance is practically zero, breaker is useless/always tripped or never trips?
        # Let's say if balance < 1.0, we just log a warning but don't blocking startup logic,
        # but here we assume we are initialized with a "good" balance.
        if self.initial_balance < 1.0:
            logger.warning("CircuitBreaker initialized with very low balance (< $1.0). Monitoring might be noisy.")

    def check(self, current_balance: float) -> bool:
        """
        Check if the circuit breaker should trip.
        Returns True if SAFE, False if TRIPPED.
        
        Note: Disabled per user request.
        """
        return True
