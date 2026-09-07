import time
import subprocess
import os
import sys
import threading

BOT_SCRIPT = "dashboard.py"
CHECK_INTERVAL_SECONDS = 10 # Check GitHub every 10 seconds

def start_web_dashboard():
    """Start the web dashboard server in a background thread."""
    try:
        import uvicorn
        from web_server import app
        print("[System] Web Dashboard starting at http://127.0.0.1:8000")
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
    except ImportError:
        print("[System] fastapi/uvicorn missing. Installing now...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "fastapi", "uvicorn"], check=True)
        # Retry after install
        import uvicorn
        from web_server import app
        print("[System] Web Dashboard starting at http://127.0.0.1:8000")
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
    except Exception as e:
        print(f"[System] Web Dashboard error: {e}")

def run_cmd(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode('utf-8').strip()
    except subprocess.CalledProcessError as e:
        return e.output.decode('utf-8').strip()

def has_updates():
    try:
        # Fetch latest from remote without merging
        subprocess.run(["git", "fetch"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Compare local HEAD with remote upstream
        status = run_cmd("git status -uno")
        
        # "Your branch is behind" is the standard git output
        return "is behind" in status
    except Exception as e:
        print(f"[Updater] Error checking for updates: {e}")
        return False

def pull_and_install():
    print("\n[Updater] 📥 Pulling latest changes from GitHub...")
    pull_output = run_cmd("git pull")
    print(pull_output)
    
    # If requirements.txt was updated, this will catch it
    print("\n[Updater] 📦 Verifying dependencies...")
    req_output = run_cmd(f'"{sys.executable}" -m pip install -r requirements.txt')
    print(req_output)

def main():
    print("="*60)
    print(" AUTO-UPDATER STARTED")
    print(f" Checking GitHub every {CHECK_INTERVAL_SECONDS} seconds for new code.")
    print("="*60)
    
    # Start web dashboard in background thread (same process, same venv)
    web_thread = threading.Thread(target=start_web_dashboard, daemon=True)
    web_thread.start()
    
    bot_process = None
    
    try:
        while True:
            # Start bot if not running
            if bot_process is None or bot_process.poll() is not None:
                print("\n[Updater] ▶️ Starting bot process...")
                # We spawn the bot using the exact same python executable (venv)
                bot_process = subprocess.Popen([sys.executable, BOT_SCRIPT])
                
            # Sleep for the interval (split into smaller chunks for clean Ctrl+C exiting)
            for _ in range(CHECK_INTERVAL_SECONDS):
                time.sleep(1)
                if bot_process and bot_process.poll() is not None:
                    if bot_process.poll() == 69:
                        print("\n" + "="*60)
                        print(" 🚨 EMERGENCY CIRCUIT BREAKER TRIPPED DETECTED 🚨")
                        print(" Auto-updater is shutting down to protect funds.")
                        print("="*60 + "\n")
                        sys.exit(69)
                    
                    print(f"\n[Updater] ⚠️ Bot process crashed or exited (code {bot_process.poll()}). Restarting immediately...")
                    break
            
            # After waiting, check for updates
            if bot_process and bot_process.poll() is None:
                if has_updates():
                    print("\n" + "="*60)
                    print(" 🔄 NEW GITHUB UPDATE DETECTED!")
                    print("="*60)
                    
                    # Kill bot cleanly
                    print("[Updater] 🛑 Stopping current bot process...")
                    bot_process.terminate()
                    try:
                        bot_process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        print("[Updater] ⚠️ Bot didn't stop cleanly. Forcing kill...")
                        bot_process.kill()
                    bot_process = None
                        
                    pull_and_install()
                    
                    print("\n[Updater] ✅ Update complete. Rebooting system...\n")
    except KeyboardInterrupt:
        print("\n[Updater] 🛑 Auto-updater stopped by user.")
        if bot_process and bot_process.poll() is None:
            bot_process.terminate()

if __name__ == "__main__":
    main()
