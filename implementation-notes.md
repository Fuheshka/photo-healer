# Photo Healer — Implementation Notes

## Prompt 1: Triage + Quarantine (2026-10-02)

### Goal
High-speed batch scanner for SSD TRIM-damaged photo archive.
Classify every image as `trim_zero` (100% zeros) or `healed_candidate` (live data behind zeroed header).

### Technical findings (from forensic analysis)

**Archive**: `E:\15407 DATA\!Problem\ВсеФотографии\` — 2385 image files, 4.45 GB

**TRIM pattern confirmed:**
- Exactly **65 536 bytes** (128 × 512-byte SSD sectors) zeroed at file start
- From offset 65 536: high-entropy compressed data (Huffman-coded JPEG bitstream)
- Pattern is 100% consistent across all 7 healed candidates — same SSD block size

**Two damage types:**
1. `trim_zero` — entire file content erased. File size survives in MFT/FAT, but every byte is 0x00. **2 345 files (98.2%)** — unrecoverable.
2. `healed_candidate` — first 65 536 bytes (JPEG header + beginning of scan) erased, remaining entropy stream intact. **7 files (0.3%)** — all repaired.

**Donor header structure (SANY0058.JPG, SANYO 8MP camera):**
```
SOI   (FF D8)              offset 0,      2 bytes
APP0  (FF E0, len=16)      offset 2,     18 bytes  — JFIF marker
APP1  (FF E1, len=46210)   offset 20, 46212 bytes  — Exif metadata
DQT   (FF DB, len=67)      offset 46232, 69 bytes  — luma quantization table
DQT   (FF DB, len=67)      offset 46301, 69 bytes  — chroma quantization table
SOF0  (FF C0, len=17)      offset 46370, 19 bytes  — frame header (3264×2448)
DHT   (FF C4) ×4            offsets 46389–46820    — Huffman tables
SOS   (FF DA, len=12)      offset 46821, 14 bytes  — scan header
─────────────────────────────────────────────────
Total header: 46 835 bytes
```

**Validation:**
- All 7 healed files decoded by `System.Drawing.Image` as 3264×2448 px
- Visual content confirmed unique per file (different scenes, not donor thumbnail)
- Double EOI guard: `heal.py` checks if live data already ends in `FF D9` before appending

### Triage algorithm (both Python and PowerShell)

```
for each file:
    stream in 64 KB chunks
    find first_nonzero offset
    if first_nonzero == -1 → trim_zero (100% zeros)
    if first_nonzero ==  0 → check magic bytes → valid or other
    if first_nonzero  >  0 → healed_candidate
```

Stream approach guarantees O(1) memory regardless of file size.
For `trim_zero` files, average read is ≤ 1 chunk (they're completely zero so we stop at EOF).
For `valid` files we stop at the first non-zero byte in chunk 1.

### Test results (Prompt 1 verification)

**Test folder: `2 класс Настя ярмарка`**
```
IMG-20221028-WA0031.jpg → trim_zero ✅  (100% zeros, size=4 308 704 bytes)
```

**Test folder: `Детские`**
```
SANY0015.JPG → healed_candidate ✅  (first_nonzero=65536, live data 4 050 253 bytes)
SANY0058.JPG → valid ✅  (FF D8 FF, 3 857 509 bytes, used as donor)
```

**Full archive scan (triage_report.json)**
| status | files | MB |
|---|---|---|
| trim_zero | 2 345 | 4 315.7 |
| healed_candidate | 7 | 20.1 |
| valid | 33 | 115.8 |

**Batch heal (heal.py)**
- All 7 candidates healed: `SANY0015,0016,0037,0038,0039,0192,1342`
- Donor auto-detected: `SANY0058.JPG` (first valid JPEG in same folder)
- Output: `*_HEALED.JPG` files created alongside originals

**Quarantine (quarantine.py)**
- 2 345 TRIM-zero files moved to `E:\15407 DATA\!Problem\QUARANTINE_TRIM_ZERO\`
- Folder structure preserved
- 4 315.7 MB freed from working archive

### Decisions and trade-offs

| Decision | Rationale |
|---|---|
| Donor header from same folder | Same camera model → same DQT/DHT/SOF0 → highest compatibility |
| 65 536 byte offset hardcoded | 100% consistent across all candidates; not guessed |
| No `--delete` flag in triage | Safety: move to quarantine only, never destroy without explicit `--delete` |
| PowerShell version | Windows users without Python; PS 5.1 is pre-installed on Win10+ |
| Double EOI guard | Some files already have `FF D9` at end of entropy stream |

### Known limitations

- Donor header compatibility: works for same-model cameras. Different cameras may need custom donor selection.
- EXIF metadata in healed files: contains donor's Exif (wrong date/GPS). To fix: use ExifTool to strip and re-inject correct EXIF from backup/sidecar.
- This triage only covers JPEG/PNG/BMP/GIF/TIFF/WEBP. RAW formats (CR2, NEF, ARW) need separate magic table entries.

### Files created / modified

| File | Status |
|---|---|
| `triage.py` | Rewritten v2 — streaming 64 KB, full spec compliance |
| `triage.ps1` | New — PowerShell port, identical logic |
| `heal.py` | Written — auto-donor + `--inplace` mode |
| `quarantine.py` | Written — folder-structure-aware quarantine |
| `README.md` | Written — full project documentation |
| `implementation-notes.md` | This file |

---

## Prompt 2: Core Refactoring (photo_healer.core & TDD) (2026-10-02)

### Goal
Extract monolithic logic into a reusable, zero-heavy-dependency package `photo_healer.core` (ITU-T T.81 marker parser, dynamic Shannon entropy TRIM boundary detector, header splicer with double-EOI suppression, and structural validator), thoroughly tested via Red-Green-Refactor TDD.

### Architecture & Module Breakdown

```
src/photo_healer/
├── __init__.py
└── core/
    ├── __init__.py         # Exports JpegParser, Marker, EntropyAnalyzer, HeaderSplicer, SpliceResult, JpegValidator, ValidationResult
    ├── parser.py           # JpegParser, Marker — strict ITU-T T.81 marker boundaries & EXIF APP1 payload isolation
    ├── entropy.py          # EntropyAnalyzer — Shannon entropy & dynamic TRIM boundary detection (512B, 4KB, 64KB, fine)
    ├── splicer.py          # HeaderSplicer, SpliceResult — donor header transplant, double-EOI guard, chunked streaming
    └── validator.py        # JpegValidator, ValidationResult — JPEG marker validation, geometry extraction, EOI check
```

### Key Technical Decisions & Solutions

1. **EXIF APP1 Payload Isolation (`JpegParser`):**
   - **Challenge:** Camera files (e.g. SANYO, Canon, Sony) frequently embed full JPEG thumbnails within EXIF APP1 (`0xFF 0xE1`). A naive byte search for `0xFF 0xDA` (SOS) matches the thumbnail's scan header, truncating the donor header prematurely before DQT, SOF0, DHT, and the primary image's SOS.
   - **Solution:** Sequential marker parser that strictly reads 16-bit big-endian length fields $L$ (which include the 2 length bytes themselves) and advances the stream pointer past the entire payload (`i += seg_len`). Marker caches avoid duplicate passes.

2. **Dynamic Shannon Entropy Boundary Detector (`EntropyAnalyzer`):**
   - **Challenge:** SSD TRIM block size varies by drive controller and OS: 512 bytes (legacy LBA), 4096 bytes (Advanced Format), or 65536 bytes (128 sectors). Stray NAND flash bit-flips in wiped sectors can fool naive non-zero checks.
   - **Solution:** Canonical Shannon entropy calculation $H(X) = -\sum p_i \log_2(p_i)$. Zeroed sectors have $H = 0.0$, while compressed JPEG Huffman streams exhibit $H \ge 7.3$. A threshold of $H \ge 5.0$ and gradient check ($\Delta H \ge 3.0$) effectively rejects isolated noise bit-flips while detecting boundaries on arbitrary sector alignments or fine-grained offsets via dense sliding window analysis.

3. **Single EOI Invariant & Memory-Efficient Streaming (`HeaderSplicer`):**
   - **Challenge:** Candidates may either terminate with a surviving `0xFF 0xD9` or be truncated mid-scan. Splicing must ensure exactly one terminal EOI without duplicating it. Furthermore, multi-gigabyte files must not be buffered entirely into RAM.
   - **Solution:** `splice_bytes()` and `splice_stream()` maintain a rolling 2-byte cross-chunk buffer to verify terminal bytes. If the live stream already ends in `\xff\xd9`, no EOI is appended; otherwise, `\xff\xd9` is written. Streaming operates in configurable chunks (default 64 KB).

4. **Zero Heavy Dependencies (Ponytail Mode):**
   - The entire core relies exclusively on Python standard library modules (`struct`, `math`, `io`, `pathlib`, `dataclasses`). `JpegValidator` uses structural stream inspection (SOI, DQT, DHT, SOF0/1/2 geometry, SOS, EOI), with optional dynamic import of Pillow for decoding verification if present in the environment.

### Test Matrix & Verification

- **Test suite:** `tests/test_parser.py`, `tests/test_entropy.py`, `tests/test_splicer.py`, `tests/test_validator.py`, and `tests/helpers.py`.
- **Synthetic Test Kit (`JPEGTestKit`):** Pure-Python factory for generating RFC/ITU-compliant synthetic JPEG segments (DQT, DHT, SOF0, SOS, nested APP1 thumbnails with fake SOS markers, corrupt markers, DRI restart intervals, and fill-byte padding).
- **Execution:** 45 tests, 100% pass rate in 0.14s:
  - 13 entropy tests (pure zeros, random high entropy, 512/1024/4096/65536 sector sizes, fine offset 1023, stray flash noise, streams, file paths).
  - 13 parser tests (normal donor, false SOS trap in EXIF APP1, 0xFF fill bytes, missing SOI/SOS, truncated lengths, DRI markers, streams, paths).
  - 9 splicer tests (double EOI prevention, missing EOI addition, auto-entropy offset, stream vs memory parity, seekable stream check).
  - 10 validator tests (healthy JPEG, non-JPEG, missing DQT/SOF, missing EOI, grayscale 1-component, trailing sector padding zeros, paths, streams).

---

## Prompt 1.4: Модуль извлечения превью (ThumbnailCarver) (2026-10-02)

### Цель
Реализовать модуль `src/photo_healer/core/carver.py` для поиска и безопасного извлечения встроенных превью (EXIF Thumbnail и полноразмерных превью высокого разрешения MPF/APP2) из частично поврежденных снимков, где основной поток изображения поврежден безвозвратно.

### Архитектура и структура решения
- **Модуль**: `src/photo_healer/core/carver.py` (реэкспорт в `photo_healer.core.__init__.py`).
- **Датаклассы**:
  - `CarvedPreview`: контейнер для извлеченного превью (`data`, `preview_type`, `width`, `height`, `size`, `source_offset`).
  - `RescueResult`: результат работы режима спасения (`status`, `data`, `preview_type`, `saved_path`, `width`, `height`, `details`).
- **Класс `ThumbnailCarver`**:
  - `extract_exif_thumbnail(source)`: парсинг TIFF-заголовка в APP1 (с сигнатурой `Exif\x00\x00`), чтение IFD0 и IFD1, извлечение тегов `0x0201` (`JPEGInterchangeFormat`) и `0x0202` (`JPEGInterchangeFormatLength`). Поддержка порядков байт `II` (Little-Endian) и `MM` (Big-Endian).
  - `extract_mpf_preview(source)`: поиск контейнеров Multi-Picture Format (CIPA DC-007) в APP2 (сигнатура `MPF\x00`), разбор MP Index IFD (теги `0xB001` `NumberOfImages` и `0xB002` `MPImageList`), извлечение вторичных кадров (Full HD 1920x1080 px и выше) с автоматическим выбором кадра с максимальным разрешением.
  - `raw_stream_carve(source)`: сигнатурный поиск независимых потоков `FF D8 FF ... FF D9` с валидацией структуры маркеров (SOF/SOS) и пропуском байт-стаффинга `FF 00` и restart-маркеров `FF D0`..`FF D7` внутри энтропийного потока.
  - `extract_best_preview(source)`: иерархический выбор наилучшего доступного превью (MPF Full HD > EXIF Thumbnail > Raw Carved).
  - `fallback_rescue(source, donor_header, save, output_dir)`: автоматический режим спасения. Если донорская трансплантация невозможна из-за сильной деградации энтропии, модуль автоматически извлекает лучшее превью.
  - `save_preview(source_path, preview_data, preview_type, output_dir)`: безопасное сохранение извлеченных миниатюр в подпапку `_Previews/` с суффиксами `_thumb.jpg` или `_preview.jpg` по правилам `careful` (исходный файл строго read-only).
  - Дескриптор `_HybridMethod`: прозрачная поддержка вызовов как на уровне класса `ThumbnailCarver.extract_exif_thumbnail(...)`, так и через экземпляр `ThumbnailCarver().extract_exif_thumbnail(...)` или `ThumbnailCarver(source).extract_exif_thumbnail()`.

### Ключевые технические решения и компромиссы
1. **Полиморфные источники данных (Zero-Copy & Memory Safety):**
   - Все методы принимают `str`, `Path`, `bytes`, `bytearray`, `io.BufferedIOBase`, `io.RawIOBase`.
2. **Толерантность к повреждению первичных заголовков:**
   - Поиск `Exif\x00\x00` и `MPF\x00` ведется сканированием по сигнатуре, поэтому превью успешно извлекаются, даже если заголовок SOI файла частично поврежден или занулен TRIM.
3. **Безопасность данных (Careful Mode):**
   - Исходные файлы архива никогда не открываются на запись. Все превью сохраняются в изолированную подпапку `_Previews/`.

### Тестирование и верификация (TDD)
- **Тестовый набор `tests/test_carver.py`**:
  - `TestThumbnailCarverExif`: 5 тестов (Little-Endian, Big-Endian, IFD0 fallback, поврежденный заголовок, отсутствие APP1).
  - `TestThumbnailCarverMpf`: 4 теста (Little-Endian, Big-Endian, выбор максимального разрешения при нескольких кадрах, отсутствие MPF).
  - `TestThumbnailCarverRawStream`: 3 теста (независимые потоки, байт-стаффинг и RST-маркеры, чистые нули).
  - `TestThumbnailCarverFallbackAndRescue`: 4 теста (приоритет MPF над EXIF, откат на EXIF, успешная трансплантация живой энтропии, спасение превью при мертвой энтропии).
  - `TestThumbnailCarverSafeSaving`: 2 теста (сохранение в `_Previews/`, сохранение суффиксов `_thumb.jpg` и `_preview.jpg`, неизменность оригинала, проверка типов аргументов).
- **Результат прогона тестов**:
  - Всего в проекте: 63 теста, 100% passed за 0.22s.
- **Прогон на реальных файлах архива (`E:\15407 DATA\!Problem\ВсеФотографии\`):**
  - Смартфон `OPPO Find X7`:
    - Извлечен EXIF Thumbnail: 30 338 байт, размер 180x240 px, верифицирован Pillow.
    - Извлечен MPF Preview: 1 009 054 байт (~1 MB), Full HD / 3MP (1536x2048 px), верифицирован Pillow.
  - Камера `SANYO 8MP` (`SANY0058.JPG`):
    - Извлечен EXIF Thumbnail: 6 181 байт, размер 160x120 px, верифицирован Pillow.
  - Поврежденный кадр камеры `SANY0015.JPG`:
    - Проверена трансплантация через `fallback_rescue`: статус `transplanted`, восстановлен кадр 3264x2448 px, верифицирован Pillow.
  - Симулированное тяжелое повреждение энтропии на снимке смартфона:
    - `fallback_rescue(..., save=True)` автоматически спас кадр в `_Previews/oppo_corrupt_preview.jpg` (1536x2048 px, 1.0 MB), верифицирован Pillow.

---

## Prompt 1.5: Ресинхронизация энтропии Хаффмана и маркеры перезапуска (StreamResync) (2026-10-02)

### Цель
Создать модуль `src/photo_healer/core/resync.py` для анализа энтропийного потока Хаффмана, автоматического обнаружения цепочек маркеров перезапуска RST0..RST7 (`0xFF 0xD0` .. `0xFF 0xD7`), расчета интервалов перезапуска DRI (`0xFF 0xDD`), компенсации фазовых сдвигов бит при кластерных разрывах и TRIM-повреждениях середины кадра, с сохранением результатов в папку `_Resync/`.

### Математическая модель и архитектура Хаффмановского декодирования

#### 1. Стандарт ITU-T T.81 / ISO/IEC 10918-1
- **Маркер DRI (Define Restart Interval, `0xFF 0xDD`)**:
  - Структура: маркер `0xFF 0xDD` + длина `0x00 0x04` + 16-битное число $R_i$.
  - Задает интервал перезапуска $R_i$ в единицах MCU (Minimum Coded Units). Если $R_i = 0$, интервалы перезапуска отключены.
- **Маркеры перезапуска RSTm (`0xFF 0xD0` .. `0xFF 0xD7`)**:
  - Двухбайтовые контрольные маркеры без поля длины.
  - Порядок следования циклический: $m = 0, 1, 2, 3, 4, 5, 6, 7, 0, 1, \dots$ по модулю 8.
  - Вставляются кодировщиком ровно через каждые $R_i$ блоков MCU. Последний MCU кадра завершается маркером `EOI` (`0xFF 0xD9`), а не RST.
- **Выравнивание на границу байта (Byte Alignment & Fill Bits)**:
  - Перед записью маркера RST последний байт энтропийного потока интервала дополняется завершающими 1-битами (от 1 до 7 единичных бит) до границы полного байта.
  - Декодер при встрече байта `0xFF` сбрасывает остаток битового буфера, восстанавливая байтовое выравнивание.
- **Сброс предиктора постоянной составляющей (DC Predictor Reset)**:
  - В базовом JPEG разностное кодирование коэффициента DC выполняется кумулятивно: $\Delta DC_k = DC_k - DC_{k-1}$.
  - В начале каждого интервала перезапуска (после SOS и после каждого маркера RST) **предикторы DC всех цветовых компонент сбрасываются в ноль** ($PRED = 0$).
  - Первый MCU после маркера RST кодирует абсолютное значение $DC_0 - 0 = DC_0$.
- **Экранирование байт-стаффингом (Byte Stuffing `0xFF 0x00`)**:
  - Любой байт `0xFF`, случайно сгенерированный битами Хаффмана, дополняется байтом `0x00`. Маркеры RST (`0xFF 0xD0..0xD7`) стаффингу не подвергаются и парсятся как управляющие сигналы.

#### 2. Физика фазового сдвига бит и локализация повреждений
- **Без маркеров RST (Continuous Entropy Stream)**:
  - Одиночный выпавший или искаженный бит сдвигает границы всех последующих переменных кодов Хаффмана (VLC). Декодер мгновенно теряет синхронизацию: дерево Хаффмана парсит мусорные частоты, а кумулятивный предиктивный DC накапливает катастрофическую цветовую погрешность (характерные диагональные сдвиги пикселей, полосы и уход всего изображения в сплошной серый градиент до конца файла).
- **С маркерами RST (Restart Interval Resilience)**:
  - Любое битовое искажение или потеря кластера **локализуется строго в пределах текущего интервала**.
  - На ближайшем валидном маркере RST декодер автоматически сбрасывает битовый буфер на границу байта, обнуляет DC-предикторы и продолжает идеальное декодирование оставшейся части кадра.

#### 3. Расчет пространственных координат (Spatial MCU Geometry)
Для кадра размером $W \times H$ при субдискретизации YCbCr 4:2:0 ($MCU = 16 \times 16$ px):
- Количество столбцов MCU: $Cols = \lceil W / 16 \rceil$
- Количество строк MCU: $Rows = \lceil H / 16 \rceil$
- Общее число MCU: $Total_{MCUs} = Cols \times Rows$
- Общее число интервалов перезапуска: $Total_{intervals} = \lceil Total_{MCUs} / R_i \rceil$
- Ожидаемое количество маркеров RST: $Total_{intervals} - 1$
- Координаты начала интервала с абсолютным индексом $k$:
  $$MCU_x = (k \cdot R_i) \pmod{Cols}, \quad MCU_y = \lfloor (k \cdot R_i) / Cols \rfloor$$
  $$Pixel_Y = MCU_y \times 16$$

### Реализация `StreamResync` (`src/photo_healer/core/resync.py`)
- **Датаклассы**:
  - `RestartMarker`: индекс (0..7), код (`0xD0`..`0xD7`), смещение в потоке, смещение начала полезной нагрузки.
  - `RestartCadence`: валидированная цепочка маркеров, доверие (confidence), шаг, список зафиксированных аномалий/разрывов.
  - `ResyncResult`: реконструированные байты, статус, первый маркер, число уцелевших маркеров, число отсеченных байт мусора, число вставленных пустых интервалов, признак добавления EOI.
- **Ключевые методы**:
  - `scan_restart_markers(data)`: потоковое сканирование с поддержкой пропуска байт-стаффинга `0xFF 0x00` и произвольных цепочек fill-байт `0xFF 0xFF...`.
  - `validate_cadence(markers, min_run)`: фильтрация случайных ложных маркеров в шуме по модулю 8, выявление разрывов последовательности с расчетом числа утраченных интервалов.
  - `extract_dri(donor)`: извлечение интервала $R_i$ из маркерного сегмента DRI (`0xFF 0xDD`).
  - `inject_dri_into_header(header, restart_interval)`: автоматическая инжекция DRI-маркера перед `SOS` (`0xFF 0xDA`), если донор не имел DRI, а поврежденное тело использует перезапуск.
  - `create_dummy_restart_interval(interval_index, restart_interval)`: генерация синтетических нейтрально-серых MCUs (4 байта на блок при стандартных таблицах Хаффмана: `0x28 0xA2 0x8A 0x00`) с завершающим маркером `RST`, позволяющая сохранить вертикальную геометрию кадра (`pad_geometry=True`).
  - `repair_tail(data)`: безопасное закрытие файла маркером EOI (`0xFF 0xD9`) с очисткой оборванных хвостов.
  - `resync_stream(source, donor, pad_geometry)`: сквозной алгоритм отсечения мусорного префикса и синхронизации с уцелевшим потоком.
  - `save_resynced(result, source_path, output_dir)`: сохранение результатов в подпапку `_Resync/` с суффиксом `_resynced.jpg`.

### Тестирование и верификация (TDD)
- **Синтетический тестовый набор `tests/test_resync.py` (20 тестов, 100% pass за 0.07s)**:
  - `TestRestartMarkerScanner`: 4 теста (чистая цепочка, пропуск стаффинга `0xFF 0x00`, обработка fill-байт `0xFF 0xFF 0xD2`, игнорирование мусорных маркеров).
  - `TestSequenceCadenceValidator`: 4 теста (идеальный шаг mod 8, фильтрация изолированных фальшивых маркеров, детекция разрывов с потерей интервалов, порог минимальной длины серии).
  - `TestDriAndExpectedMarkers`: 4 теста (парсинг DRI, отсутствие DRI, расчет ожидаемого числа маркеров, расчет координат MCU/Pixel Y).
  - `TestCorruptedPrefixResynchronization`: 2 теста (отсечение 64 КБ нулей TRIM, отсечение случайного шума с ложными маркерами).
  - `TestDummyIntervalPadding`: 1 тест (вставка dummy-интервалов для сохранения геометрии).
  - `TestTruncatedTailAndEoiGuards`: 2 теста (обрыв на середине интервала, предотвращение двойного EOI).
  - `TestEndToEndResyncWorkflow`: 3 теста (инжекция DRI в донорский заголовок, сохранение в `_Resync/`, полиморфизм Bytes/Stream/Path).
- **Суммарный регрессионный прогон всего проекта**:
  - **83 теста, 100% passed за 0.39s**.
- **Прогон на реальных фото архива (`E:\15407 DATA\!Problem\` / `IMG_20220905_162838.jpg`, 7.2 MB, 4000x3000 px)**:
  - Обнаружен DRI $R_i = 20$ MCUs, 75 RST маркеров в скане.
  - Симуляция 150 КБ TRIM-зануления: `StreamResync` синхронизировался на маркере `RST2`, отсек 179 544 байт мусора, сохранил в `_Resync/IMG_20220905_162838_resynced.jpg`.
  - Валидация Pillow (`PIL.Image`): файл успешно открыт и растрирован в RGB-пиксели памяти 4000x3000 px без фатальных сбоев.
  - Проверка геометрического выравнивания (`pad_geometry=True`): сгенерированы dummy-интервалы, сохранено в `_Resync/IMG_20220905_162838_padded_resynced.jpg`, растр успешно декодирован.
  - Симуляция инверсии бит в середине интервала 5: подтверждена полная локализация артефактов strictly внутри интервала 5 с мгновенным восстановлением чистого декодирования на маркере RST5.



