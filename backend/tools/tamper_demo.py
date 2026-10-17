"""Demo only: silently edits one past audit record so the dashboard shows the
hash chain breaking. Usage (server may keep running): python tools/tamper_demo.py [record_id]"""
import sqlite3
import sys
from pathlib import Path

rid = int(sys.argv[1]) if len(sys.argv) > 1 else 5
db = Path(__file__).resolve().parents[1] / "medgate.db"
con = sqlite3.connect(db)
n = con.execute("UPDATE audit SET detail=? WHERE id=?", ('{"forged":true}', rid)).rowcount
con.commit()
print(f"edited audit record {rid}" if n else f"no audit record {rid}")
