"""Удаление демонстрационных данных из базы.

Скрипт удаляет связи, алерты с is_demo=1 и сам источник «demo».
Реальные данные (Telegram/RSS) не затрагиваются.
"""

import sqlite3
from pathlib import Path

DB = Path(r"C:\Users\Valya\Documents\Проект по умолчанию\drone-radar\backend\radar.db")

con = sqlite3.connect(DB)
con.execute("PRAGMA foreign_keys = ON")
cur = con.cursor()

demo_ids = [row[0] for row in cur.execute("select id from alerts where is_demo = 1")]
print("демо-алертов:", len(demo_ids))

if demo_ids:
    placeholders = ",".join("?" for _ in demo_ids)
    cur.execute(f"delete from alert_sources where alert_id in ({placeholders})", demo_ids)
    print("удалено связей:", cur.rowcount)
    cur.execute(f"delete from alerts where id in ({placeholders})", demo_ids)
    print("удалено алертов:", cur.rowcount)

cur.execute("delete from sources where kind = 'demo'")
print("удалено источников demo:", cur.rowcount)

con.commit()

print("осталось алертов:", cur.execute("select count(*) from alerts").fetchone()[0])
print("по источникам:")
for row in cur.execute(
    "select s.kind, s.name, count(*) from alert_sources l join sources s on s.id = l.source_id group by 1,2 order by 3 desc"
):
    print("  ", row)
con.close()
