"""python -m switchboard.scripts.init_db -- creates switchboard.db."""
from switchboard.db import init_db

if __name__ == "__main__":
    init_db()
    print("Switchboard database initialized.")
