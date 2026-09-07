"""
Structured Logging Configuration using Loguru.
Separates logs into different files as requested:
- Noise (Debug/Trace)
- Success (Trades/Opportunities)
- Fails (Errors/Warnings)
- Status (Info/General)
"""

import sys
from pathlib import Path
from loguru import logger

# Define log directory
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Define file paths
LOG_NOISE = LOG_DIR / "noise.log"
LOG_SUCCESS = LOG_DIR / "success.log"
LOG_FAILS = LOG_DIR / "fails.log"
LOG_DEBUG = LOG_DIR / "debug.log"
LOG_STATUS = LOG_DIR / "status.log"

def get_client_log_dir(client_tag: str) -> Path:
    """Get or create a specific log directory for a client."""
    client_dir = LOG_DIR / client_tag
    client_dir.mkdir(parents=True, exist_ok=True)
    return client_dir

def setup_logging():
    """Configure loguru logger with dynamic sinks for clients."""
    
    # Remove default handler
    logger.remove()
    
    # 1. Console Handler
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        filter=lambda record: "client" not in record["extra"]  # Only print system info to console to keep dash clean
    )

    # 2. Universal / System Logs (Only for logs WITHOUT a client bound to them)
    sys_filter = lambda record: "client" not in record["extra"]
    
    logger.add(
        LOG_DIR / "system" / "noise.log",
        level="DEBUG",
        rotation="10 MB",
        retention="1 day",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        filter=lambda record: sys_filter(record) and (record["level"].name == "DEBUG" or record["level"].name == "TRACE")
    )
    
    logger.add(
        LOG_DIR / "system" / "success.log",
        level="SUCCESS",
        rotation="10 MB",
        retention="1 week",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        filter=lambda record: sys_filter(record) and record["level"].name == "SUCCESS"
    )

    logger.add(
        LOG_DIR / "system" / "fails.log",
        level="WARNING",
        rotation="10 MB",
        retention="1 week",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        filter=lambda record: sys_filter(record) and record["level"].no >= logger.level("WARNING").no
    )

    logger.add(
        LOG_DIR / "system" / "debug.log",
        level="DEBUG",
        rotation="50 MB",
        retention="3 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        filter=sys_filter
    )

    # 3. Dynamic Per-Client Logs
    # This filter ensures a log only goes to the client log file if `record["extra"]["client"]` matches the file's expected client
    def client_filter(client_tag, level_name=None, is_fail=False):
        def f(record):
            if record["extra"].get("client") != client_tag:
                return False
            if is_fail:
                return record["level"].no >= logger.level("WARNING").no
            if level_name:
                return record["level"].name == level_name
            return True
        return f

    # We will defer dynamic sink creation until a bot asks for it, 
    # to avoid creating empty folders for clients that don't spin up.
    return logger

def bind_client_logger(client_tag: str):
    """
    Called by bot instances to create their own bound logger and register sinks.
    """
    client_dir = get_client_log_dir(client_tag)
    
    # We only register a client's sinks ONCE. 
    # Loguru will write to them based on the `client` extra tag.
    # To prevent duplicate sinks, we can check if the folder existed, but loguru handles same-file sinks fine, 
    # actually it's better to just ensure we don't bind multiple times per tag if we can avoid it.
    
    logger.add(
        client_dir / "debug.log",
        level="DEBUG",
        rotation="10 MB",
        retention="3 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        filter=lambda record: record["extra"].get("client") == client_tag
    )
    
    logger.add(
        client_dir / "success.log",
        level="SUCCESS",
        rotation="10 MB",
        retention="1 week",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        filter=lambda record: record["extra"].get("client") == client_tag and record["level"].name == "SUCCESS"
    )
    
    logger.add(
        client_dir / "fails.log",
        level="WARNING",
        rotation="10 MB",
        retention="1 week",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        filter=lambda record: record["extra"].get("client") == client_tag and record["level"].no >= logger.level("WARNING").no
    )
    
    return logger.bind(client=client_tag)

# Initialize system sinks on import
setup_logging()
