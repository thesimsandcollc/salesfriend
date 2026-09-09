"""Run once to create salesfriend.db: `python -m scripts.init_db`"""
from app.storage.db import init_db

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
