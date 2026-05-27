"""Kill old uvicorn and start fresh."""
import subprocess, time, os

# Kill old
subprocess.run(
    "ps aux | grep uvicorn | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null",
    shell=True, capture_output=True
)
time.sleep(2)

# Start fresh
os.chdir('/Users/vaibhav/Documents/IPOScraper')
subprocess.Popen(
    ['.venv/bin/uvicorn', 'app.main:app', '--port', '8001'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(3)

# Verify
r = subprocess.run(['curl', '-s', 'http://127.0.0.1:8001/health'], capture_output=True, text=True)
print(r.stdout)
