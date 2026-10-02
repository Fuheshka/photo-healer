# Руководство по участию в разработке (Contributing to Photo Healer)

Спасибо за интерес к проекту **Photo Healer**! Любой вклад, будь то отчет об ошибке, предложение новой функции, улучшение документации или пулл-реквест с кодом, очень ценен для развития инструмента.

---

## Как помочь проекту

### 1. Сообщение о багах и проблемах
Если вы столкнулись с ошибкой при сканировании, неверным определением поврежденных файлов или сбоем интерфейса:
1. Проверьте список [открытых Issues](https://github.com/Fuheshka/photo-healer/issues), чтобы убедиться, что проблема еще не обсуждается.
2. Если ошибки нет в списке, создайте новый [Bug report](https://github.com/Fuheshka/photo-healer/issues/new?template=bug_report.md).
3. Обязательно укажите операционную систему, версию Python, шаги для воспроизведения и приложите консольный вывод или hex-дамп первых байт проблемного файла.

### 2. Предложение новых идей и алгоритмов
Есть идея для нового функционала, поддержки RAW-форматов или улучшения интерфейса?
- Откройте [Feature request](https://github.com/Fuheshka/photo-healer/issues/new?template=feature_request.md).
- Опишите сценарий использования, структуру формата или алгоритмический подход.

### 3. Тестирование на разных конфигурациях
Если у вас есть архивы поврежденных фотографий с разных моделей камер (Canon, Nikon, Sony, SANYO, Olympus, смартфоны), флеш-накопителей или SSD, испытание утилиты и обратная связь в Issues бесценны.

### 4. Доработка документации и локализации
- Исправление опечаток, дополнение инструкций и реальных кейсов восстановления.
- Поддержка паритета двуязычной документации (`README.md` на английском и `README.ru.md` на русском).
- Дополнение словарей локализации интерфейса (`src/photo_healer/cli/i18n.py` и `src/photo_healer/gui/i18n.py`).

### 5. Написание кода (Pull Requests)
Мы рады пулл-реквестам! Чтобы процесс интеграции прошел быстро и прозрачно, следуйте рекомендациям ниже.

---

## Настройка рабочего окружения

Проект разрабатывается на Python 3.10+ с использованием стандартной библиотеки для CLI и ядра, а также PySide6 / Pillow для опционального графического интерфейса.

### 1. Клонирование репозитория
```bash
git clone https://github.com/Fuheshka/photo-healer.git
cd photo-healer
```

### 2. Создание и активация виртуального окружения
- **Windows (PowerShell):**
  ```powershell
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  ```
- **Linux / macOS:**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

### 3. Установка пакета в режиме разработки
Установите проект с зависимостями для тестов (`dev`) и графического интерфейса (`gui`):
```bash
pip install --upgrade pip
pip install -e ".[dev,gui]"
```

Проверьте корректность установки:
```bash
photo-healer --version
photo-healer --help
```

---

## Стандарты кода и архитектурные принципы

### 1. Простота и минимализм (Ponytail / YAGNI)
- **Zero-dependency для ядра и CLI:** Модули `photo_healer.core` и базовая консольная часть `photo_healer.cli` должны опираться исключительно на стандартную библиотеку Python (`struct`, `pathlib`, `typing`, `argparse`, `urllib`). Не добавляйте внешние зависимости без предварительного обсуждения в Issues.
- Решайте коренную причину (root cause), а не маскируйте симптомы.
- Минимальный diff: самый короткий, надежный и понятный вариант реализации побеждает.

### 2. Стиль оформления кода
- Соблюдение стандарта PEP 8.
- Форматирование кода по стандартам Black (длина строки до 100 символов) и проверка линтером Flake8.
- Полная статическая типизация функций (`type hints` из модуля `typing`).

### 3. Стандарты коммитов (Commit Shield)
- **Язык коммитов:** Все сообщения коммитов `git commit` оформляются **исключительно на английском языке** по спецификации Conventional Commits:
  - `feat(core): add restart marker resynchronization`
  - `feat(gui): implement zoom controls in split preview`
  - `fix(triage): handle zero-byte files gracefully`
  - `refactor(splicer): simplify entropy offset detection`
  - `test(carver): add unit test for MPF thumbnail extraction`
  - `docs(contributing): update pytest execution guide`
- **Запрет автокоммитов:** Никаких слепых или автоматических коммитов рабочей копии. Тщательно проверяйте полный `git status` и `git diff` перед фиксацией.

---

## Порядок тестирования и TDD

Проект покрыт более чем 390 тестами, обеспечивающими защиту от регрессий. При внесении изменений разработка ведется по методологии TDD (Test-Driven Development):

1. **Тест воспроизведения:** Напишите тест в `tests/test_*.py`, воспроизводящий баг или описывающий ожидаемое поведение новой фичи (тест падает - Red).
2. **Минимальная реализация:** Напишите необходимый код в `src/photo_healer/`, чтобы тест прошел успешно (тест проходит - Green).
3. **Рефакторинг:** Упростите и отполируйте код, убедившись, что все тесты по-прежнему зеленые (Refactor).

### Запуск тестов
- Запуск всего тестового набора:
  ```bash
  pytest
  # или
  python -m pytest
  ```

- Запуск конкретного тестового файла:
  ```bash
  pytest tests/test_carver.py -v
  pytest tests/test_resync.py -v
  pytest tests/test_updater.py -v
  pytest tests/test_gui.py -v
  ```

- Запуск тестов GUI в headless-окружении (CI / Linux без X-сервера):
  ```bash
  QT_QPA_PLATFORM=offscreen pytest tests/test_gui_views.py -v
  ```

Все существующие и новые тесты должны выполняться со 100% успехом перед отправкой Pull Request.

---

## Порядок отправки изменений (Pull Request Workflow)

1. Сделайте **Fork** репозитория на GitHub.
2. Создайте тематическую ветку от актуальной ветки `master`:
   ```bash
   git checkout -b feat/my-awesome-feature
   # или для исправления ошибки:
   git checkout -b fix/issue-description
   ```
3. Внесите изменения и напишите тесты.
4. Убедитесь, что все тесты проходят: `python -m pytest`.
5. Зафиксируйте изменения понятными коммитами на английском языке:
   ```bash
   git add .
   git commit -m "feat(core): support custom donor header search depth"
   ```
6. Отправьте ветку в свой форк:
   ```bash
   git push origin feat/my-awesome-feature
   ```
7. Откройте **Pull Request** в репозиторий `Fuheshka/photo-healer`. Заполните чек-лист шаблона PR.

---

## Звёздочка на GitHub ⭐️

Если проект помог вам спасти ценные фотографии, лучший способ выразить благодарность: поставить **звёздочку (Star)** репозиторию [github.com/Fuheshka/photo-healer](https://github.com/Fuheshka/photo-healer)!
