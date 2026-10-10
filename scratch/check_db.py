import sqlite3

conn = sqlite3.connect("storage/shorts_automation.db")
cur = conn.cursor()
cur.execute("PRAGMA table_info(clips)")
for col in cur.fetchall():
    print(col)
