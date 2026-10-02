#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
"""
Photo Healer - quarantine.py
Перемещает TRIM-пустышки (trim_zero) из triage_report.json в папку карантина.
Сохраняет относительную структуру папок. Не удаляет — только перемещает.

Использование:
  python quarantine.py --report triage_report.json --dest E:\\Quarantine
  python quarantine.py --report triage_report.json --dest E:\\Quarantine --dry-run
  python quarantine.py --report triage_report.json --dest E:\\Quarantine --delete
"""

import json
import shutil
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Photo Healer - quarantine TRIM-zero files")
    parser.add_argument("--report", required=True, help="triage_report.json от triage.py")
    parser.add_argument("--dest", required=True, help="Папка карантина (будет создана)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что будет сделано, не трогать файлы")
    parser.add_argument("--delete", action="store_true",
                        help="Удалять вместо перемещения (НЕОБРАТИМО, требует подтверждения)")
    parser.add_argument("--root", default=None,
                        help="Корневая папка архива для сохранения структуры подпапок")
    args = parser.parse_args()

    with open(args.report, encoding='utf-8') as f:
        records = json.load(f)

    zeros = [r for r in records if r.get('status') == 'trim_zero']
    total_size = sum(r.get('size', 0) for r in zeros)

    print(f"Trim-zero files: {len(zeros)}  ({total_size / 1_048_576:,.1f} MB)")

    if not zeros:
        print("Nothing to do.")
        return

    if args.delete and not args.dry_run:
        print("\n!!! --delete: файлы будут УДАЛЕНЫ НАВСЕГДА !!!")
        confirm = input("Введите DELETE чтобы подтвердить: ").strip()
        if confirm != "DELETE":
            print("Отменено.")
            return

    dest_root = Path(args.dest)
    if not args.dry_run:
        dest_root.mkdir(parents=True, exist_ok=True)

    # Определяем общий корень для сохранения структуры
    if args.root:
        archive_root = Path(args.root)
    else:
        # Автоопределение: общий префикс всех путей
        all_paths = [Path(r['path']) for r in zeros]
        archive_root = Path(all_paths[0].anchor)
        for p in all_paths:
            try:
                p.relative_to(archive_root)
            except ValueError:
                archive_root = p.parent

    moved = skipped = errors = 0
    freed = 0

    for r in zeros:
        src = Path(r['path'])
        if not src.exists():
            skipped += 1
            continue

        # Сохраняем структуру подпапок
        try:
            rel = src.relative_to(archive_root)
        except ValueError:
            rel = Path(src.name)

        dst = dest_root / rel
        size = r.get('size', 0)

        if args.dry_run:
            print(f"  [DRY] {src} -> {dst}  ({size / 1024:.0f} KB)")
            moved += 1
            freed += size
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            if args.delete:
                src.unlink()
                print(f"  [DEL] {src.name}  ({size / 1024:.0f} KB)")
            else:
                shutil.move(str(src), str(dst))
                print(f"  [MOV] {src.name} -> {rel}")
            moved += 1
            freed += size
        except OSError as e:
            print(f"  [ERR] {src.name}: {e}")
            errors += 1

    action = "Would move" if args.dry_run else ("Deleted" if args.delete else "Moved")
    print(f"\n{action}: {moved} files  ({freed / 1_048_576:,.1f} MB freed)")
    if skipped:
        print(f"Skipped (already gone): {skipped}")
    if errors:
        print(f"Errors: {errors}")
    if not args.dry_run and not args.delete:
        print(f"Quarantine: {dest_root}")


if __name__ == "__main__":
    main()
