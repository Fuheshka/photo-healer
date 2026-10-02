#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
"""
Photo Healer - heal.py
Берёт кандидатов из heal_candidates.json (или triage_report.json),
находит донора в той же папке, трансплантирует JPEG-заголовок,
сохраняет вылеченный файл рядом (или заменяет оригинал с --inplace).

Использование:
  python heal.py --candidates heal_candidates.json [--inplace] [--donor <файл>]
  python heal.py --scan <папка> [--inplace]
"""

import os
import json
import argparse
import shutil
from pathlib import Path
from dataclasses import dataclass

JPEG_SOI = b'\xff\xd8\xff'
TARGET_DATA_OFFSET = 65536   # первый живой байт (паттерн TRIM: 128 секторов × 512 байт)


# ── Работа с JPEG-заголовком ─────────────────────────────────────────────────

def extract_donor_header(donor_bytes: bytes) -> bytes:
    """
    Парсит донора и возвращает байты от SOI до конца SOS-сегмента включительно.
    Это всё, что нужно для восстановления: таблицы DQT, DHT, SOF0 и маркер начала скана.
    """
    i = 0
    n = len(donor_bytes)
    while i < n - 1:
        if donor_bytes[i] != 0xFF:
            i += 1
            continue
        marker = donor_bytes[i + 1]
        if marker == 0x00 or marker == 0xFF:
            i += 1
            continue
        if marker == 0xD8:   # SOI — нет длины
            i += 2
            continue
        if marker == 0xDA:   # SOS — начало энтропийного потока
            seg_len = (donor_bytes[i + 2] << 8) | donor_bytes[i + 3]
            end = i + 2 + seg_len   # конец SOS-сегмента (перед битовым потоком)
            return donor_bytes[:end]
        if i + 3 >= n:
            break
        seg_len = (donor_bytes[i + 2] << 8) | donor_bytes[i + 3]
        i += 2 + seg_len
    raise ValueError("SOS marker not found in donor — not a valid JPEG?")


def find_donor_in_folder(folder: Path, exclude: Path = None) -> Path | None:
    """Ищет первый валидный JPEG (FF D8 FF) в папке, пропуская exclude."""
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in {'.jpg', '.jpeg'}:
            continue
        if exclude and f.resolve() == exclude.resolve():
            continue
        try:
            with open(f, 'rb') as fh:
                head = fh.read(3)
            if head == JPEG_SOI:
                return f
        except OSError:
            continue
    return None


# ── Лечение одного файла ──────────────────────────────────────────────────────

@dataclass
class HealResult:
    path: str
    status: str          # healed | skipped | error
    donor: str = ""
    output: str = ""
    note: str = ""


def heal_file(target_path: Path, donor_path: Path, inplace: bool) -> HealResult:
    try:
        donor_bytes = target_path.read_bytes() if False else donor_path.read_bytes()
        target_bytes = target_path.read_bytes()
    except OSError as e:
        return HealResult(str(target_path), "error", note=str(e))

    # Проверяем донора
    if donor_bytes[:3] != JPEG_SOI:
        return HealResult(str(target_path), "error",
                          donor=str(donor_path),
                          note="donor is not a valid JPEG")

    # Извлекаем заголовок донора
    try:
        header = extract_donor_header(donor_bytes)
    except ValueError as e:
        return HealResult(str(target_path), "error",
                          donor=str(donor_path), note=str(e))

    # Живые данные из target, начиная с first_nonzero
    live_data = target_bytes[TARGET_DATA_OFFSET:]

    # Убираем двойной EOI: если live_data уже заканчивается на FF D9 — не добавляем ещё
    if live_data[-2:] == b'\xff\xd9':
        eoi = b''
    else:
        eoi = b'\xff\xd9'

    result_bytes = header + live_data + eoi

    # Определяем путь вывода
    if inplace:
        # Делаем backup оригинала рядом (.bak)
        bak = target_path.with_suffix('.bak')
        if not bak.exists():
            shutil.copy2(target_path, bak)
        out_path = target_path
    else:
        out_path = target_path.with_stem(target_path.stem + '_HEALED')

    try:
        out_path.write_bytes(result_bytes)
    except OSError as e:
        return HealResult(str(target_path), "error",
                          donor=str(donor_path), note=f"write failed: {e}")

    return HealResult(
        path=str(target_path),
        status="healed",
        donor=str(donor_path),
        output=str(out_path),
        note=f"header={len(header)}b live={len(live_data)}b total={len(result_bytes)}b"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Photo Healer - header transplant")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--candidates", help="heal_candidates.json от triage.py")
    group.add_argument("--scan", help="Папка для прямого сканирования кандидатов")
    parser.add_argument("--donor", help="Принудительный файл-донор (иначе ищем в папке цели)")
    parser.add_argument("--inplace", action="store_true",
                        help="Заменить оригиналы (бэкап → .bak). По умолчанию: _HEALED рядом")
    parser.add_argument("--report", default="heal_report.json",
                        help="Путь для JSON-отчёта (default: heal_report.json)")
    args = parser.parse_args()

    # Собираем список кандидатов
    candidates = []
    if args.candidates:
        with open(args.candidates, encoding='utf-8') as f:
            data = json.load(f)
        candidates = [Path(r['path']) for r in data
                      if r.get('status') == 'healed_candidate']
    else:
        # Прямое сканирование папки
        root = Path(args.scan)
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                if Path(fname).suffix.lower() not in {'.jpg', '.jpeg'}:
                    continue
                fpath = Path(dirpath) / fname
                try:
                    with open(fpath, 'rb') as fh:
                        head = fh.read(3)
                    if head == b'\x00\x00\x00':
                        candidates.append(fpath)
                except OSError:
                    pass

    if not candidates:
        print("No candidates found.")
        return

    print(f"Candidates: {len(candidates)} files")
    mode = "--inplace (backup → .bak)" if args.inplace else "_HEALED suffix"
    print(f"Mode: {mode}\n")

    forced_donor = Path(args.donor) if args.donor else None

    results = []
    ok = err = skip = 0

    for target in candidates:
        # Выбор донора
        if forced_donor:
            donor = forced_donor
        else:
            donor = find_donor_in_folder(target.parent, exclude=target)
            if donor is None:
                r = HealResult(str(target), "skipped",
                               note="no valid donor found in same folder")
                results.append(r)
                skip += 1
                print(f"  [SKIP] {target.name} — нет донора в папке")
                continue

        r = heal_file(target, donor, args.inplace)
        results.append(r)

        if r.status == "healed":
            ok += 1
            out_name = Path(r.output).name
            print(f"  [OK  ] {target.name} -> {out_name}  ({r.note})")
        else:
            err += 1
            print(f"  [ERR ] {target.name}: {r.note}")

    print(f"\nDone: {ok} healed, {skip} skipped, {err} errors")

    # Сохраняем отчёт
    report_path = Path(args.report)
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump([{'path': r.path, 'status': r.status,
                    'donor': r.donor, 'output': r.output, 'note': r.note}
                   for r in results], f, ensure_ascii=False, indent=2)
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
