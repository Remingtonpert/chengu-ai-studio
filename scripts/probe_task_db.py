"""只读查询任务库：最近任务的 prompt 原文与类型、错误信息、产物路径。"""
import sqlite3

DB = r"C:\Users\cdchk\AppData\Local\ChenguAIStudio\studio.sqlite3"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = list(con.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 12"))
for r in rows:
    d = dict(r)
    print("=" * 70)
    print(d.get("task_id"), d.get("engine_id"), d.get("task_type"), d.get("status"), d.get("created_at"))
    if d.get("error_message"):
        print("ERROR:", str(d["error_message"])[:200])
    p = d.get("prompt")
    print("prompt:", type(p).__name__, repr(p)[:180])
    print("params:", str(d.get("parameters_json"))[:220])
    print("outputs:", str(d.get("output_files_json"))[:320])
con.close()
