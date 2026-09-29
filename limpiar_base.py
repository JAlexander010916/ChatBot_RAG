
import sqlite3
from backend.config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("DELETE FROM chunks")
cur.execute("DELETE FROM documents")
conn.commit()
conn.close()
print("DB limpiada")
