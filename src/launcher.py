import os
import sys
import time
import subprocess
import signal

# Configuration
BOT_SCRIPT = "dashboard.py"
CHECK_INTERVAL = 60  # Check for updates every 60 seconds

def run_git_command(command):
    """Run a git command and return output."""
    try:
        result = subprocess.run(
            command, 
            cwd=os.getcwd(), 
            shell=True,
            capture_output=True, 
            text=True
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"Git Error: {e}")
        return ""

def check_for_updates():
    """Check if remote has new commits."""
    print("🔍 Checking for updates...", end=" ", flush=True)
    
    # Update remote refs
    run_git_command("git fetch")
    
    # Check status
    status = run_git_command("git status -uno")
    
    if "Your branch is behind" in status:
        print("⚡ UPDATE DETECTED!")
        return True
    
    print("✅ Up to date.")
    return False

def main():
    print("🚀 STARTING HOLY GRAIL BOT WITH AUTO-UPDATER")
    print(f"   Launcher PID: {os.getpid()}")
    print("=================================================")
    
    bot_process = None
    
    print("📦 Verifying dependencies on startup...")
    print(run_git_command(f"{sys.executable} -m pip install -r requirements.txt"))
    
    try:
        while True:
            # 1. Update Code if needed (before starting or restarting)
            if check_for_updates():
                print("📥 Pulling changes...")
                print(run_git_command("git pull"))
                print("📦 Installing dependencies...")
                print(run_git_command(f"{sys.executable} -m pip install -r requirements.txt"))
                print("✅ Update complete.")
            
            # 2. Start Bot
            print(f"▶️ Launching Main UI ({BOT_SCRIPT})...")
            # Use global python directly
            bot_process = subprocess.Popen([sys.executable, BOT_SCRIPT])
            
            # 3. Monitor Loop
            while bot_process.poll() is None:
                time.sleep(CHECK_INTERVAL)
                
                if check_for_updates():
                    print("🔄 Restarting bot for update...")
                    
                    # Graceful shutdown attempt
                    bot_process.terminate()
                    try:
                        bot_process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        print("⚠️ Force killing processes...")
                        bot_process.kill()
                    
                    # Break inner loop to restart outer loop (which pulls and restarts)
                    break
            
            # If bot exited naturally (crash or Ctrl+C from user in theory), we restart
            print("⚠️ Bot process exited. Restarting in 5 seconds...")
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\n🛑 Launcher stopped by user.")
        if bot_process:
            bot_process.terminate()
    except Exception as e:
        print(f"\n❌ Launcher crashed: {e}")

if __name__ == "__main__":
    main()
