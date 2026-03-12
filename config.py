import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")
SF_ENCRYPTION_KEY = os.environ.get("SF_ENCRYPTION_KEY", "")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
MAX_CONCURRENT_BROWSERS = int(os.getenv("MAX_CONCURRENT_BROWSERS", "4"))
BOT_IDLE_TIMEOUT = int(os.getenv("BOT_IDLE_TIMEOUT", "600"))  # 10 min
PROFILES_DIR = os.getenv("PROFILES_DIR", "/data/profiles")
ERRORS_DIR = os.getenv("ERRORS_DIR", "/data/errors")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
WATCHDOG_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL", "60"))
STUCK_THRESHOLD = int(os.getenv("STUCK_THRESHOLD", "300"))  # 5 min
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/tmp/sf-worker-heartbeat")
