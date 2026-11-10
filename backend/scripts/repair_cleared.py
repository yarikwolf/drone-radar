"""Починка статусов ``cleared`` после backfill.

Раньше отбой закрывал все активные алерты региона независимо от времени
события. Из-за этого при чтении истории каналов старый отбой мог закрыть
более новую тревогу, и регион ошибочно считался «завершённым».

Скрипт восстанавливает тревогу, если её время события позже последнего
отбоя в этом регионе (значит, отбоя после неё не было).

Запуск без аргументов — только показать изменения. Применить: ``--apply``.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "radar.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Восстановление неверно закрытых алертов")
    parser.add_argument("--apply", action="store_true", help="записать изменения в БД")
    args = parser.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Последний отбой по каждому региону.
    last_clear: dict[int, datetime] = {}
    for row in cur.execute(
        "select region_id, max(event_at) as clear_at from alerts "
        "where status = 'all_clear' and region_id is not null group by region_id"
    ):
        raw = row["clear_at"]
        if raw:
            last_clear[row["region_id"]] = datetime.fromisoformat(str(raw))

    rows = cur.execute(
        "select id, region_id, priority, status, event_at, cleared_at, title "
        "from alerts where status = 'cleared' order by event_at"
    ).fetchall()

    changes: list[tuple[int, str, str, str]] = []
    for row in rows:
        clear_at = last_clear.get(row["region_id"])
        event_at = datetime.fromisoformat(str(row["event_at"]))
        if clear_at is not None and event_at <= clear_at:
            continue  # отбой действительно был позже — запись закрыта верно

        restored = "warning" if (row["priority"] or 0) <= 1 else "active"
        changes.append((row["id"], row["status"], restored, row["title"]))

    print(f"проверено закрытых алертов: {len(rows)}")
    print(f"будет восстановлено: {len(changes)}")
    for alert_id, old_status, new_status, title in changes:
        print(f"  #{alert_id}: {old_status} -> {new_status} | {title}")

    if not args.apply:
        print("режим проверки: запустите с --apply, чтобы записать изменения")
        con.close()
        return

    if not changes:
        con.close()
        return

    backup = DB.with_suffix(f".db.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copyfile(DB, backup)
    print(f"резервная копия: {backup}")

    cur.executemany(
        "update alerts set status = ?, cleared_at = null where id = ?",
        [(new_status, alert_id) for alert_id, _, new_status, _ in changes],
    )
    updated = cur.rowcount
    con.commit()

    remaining = cur.execute("select count(*) from alerts where status = 'cleared'").fetchone()[0]
    print(f"обновлено: {updated}")
    print(f"осталось закрытых алертов: {remaining}")
    con.close()


if __name__ == "__main__":
    main()
