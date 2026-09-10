"""python -m switchboard.scripts.create_api_key "some label" -- mints a new
API key from the command line (the dashboard's /dashboard/api-keys page does
the same thing for anyone who'd rather click a button)."""
import sys

from switchboard.db import create_api_key, init_db

if __name__ == "__main__":
    init_db()
    label = sys.argv[1] if len(sys.argv) > 1 else "cli"
    _, plaintext = create_api_key(label)
    print(f"Created API key ({label}) -- store it now, it won't be shown again:\n{plaintext}")
