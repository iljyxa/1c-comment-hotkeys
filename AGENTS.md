# AGENTS.md — памятка для AI-агентов и новых участников

Этот файл описывает, как устроен проект, как в нём принято работать и какие
правила обязательны. Подробности архитектуры — в `ARCHITECTURE.md`,
пользовательская документация — в `README.md` и `QUICKSTART.md`.

## Что это

Windows-утилита в системном трее для быстрого шаблонного комментирования кода
(в первую очередь 1С): выделил фрагмент → нажал глобальную горячую клавишу →
выбрал шаблон → результат вставлен на место выделения. Шаблоны могут
подставлять номер и название задачи Jira из настроенного источника.

Платформа — **только Windows**. Linux/macOS не поддерживаются и не
заявлены: глобальные хоткеи и эмуляция ввода через `pynput`, DPAPI, работа с
буфером и окнами через `ctypes.windll`. Не тратьте усилия на кроссплатформенность.

## Стек

- Python 3.12 (CI), PySide6-Essentials (Qt 6), `pynput` (хоткеи и эмуляция
  Ctrl+C/Ctrl+V), `pyperclip` (буфер обмена).
- Зависимости: `requirements.txt`. Сторонних зависимостей минимум — новые
  добавлять только при явной необходимости (сборка onefile и так ~30 МБ).
- Тестового фреймворка и линтера в репозитории нет.

## Структура

```
src/app.py                       точка входа, класс Application: связывает сервисы и UI,
                                 глобальные хоткеи, захват текста, сценарий вставки,
                                 планировщик автообновления Jira
src/core/
  hotkeys.py                     HotkeyManager — глобальные хоткеи через pynput.Listener
  clipboard_service.py           захват выделения (Ctrl+C), применение шаблона, вставка (Ctrl+V),
                                 сохранение/восстановление исходного буфера
  template_engine.py             макросы {text} {date} {author} {issue_key}..., модификаторы,
                                 блочные директивы {@line_limit}...{@end}
  comments_repository.py         шаблоны комментариев → comments.json
  settings_repository.py         настройки приложения → config.json
  jira_sources_repository.py     источники Jira → jira_sources.json (токены зашифрованы)
  secret_store.py                Windows DPAPI через ctypes, формат "dpapi:<base64>"
  jira_issues_service.py         запросы к Jira REST (/rest/api/2/search), stale-while-revalidate
  jira_issues_cache.py           кэш задач → jira_issues_cache.json
  jira_last_issue_repository.py  последняя выбранная задача → jira_last_issue.json
  atomic_io.py                   atomic_write_json, backup_corrupted_file
  config_paths.py                каталог %APPDATA%\1CCommentHotkeys (+ миграция из MS Store-пути)
src/ui/
  main_window.py                 главное окно, трей, диалог редактирования шаблона,
                                 диалог источников Jira (JiraSourcesDialog)
  comment_dialog.py              выбор шаблона по хоткею
  issue_dialog.py                выбор задачи Jira
src/resources_rc.py              сгенерированный Qt-ресурс (иконка); не править руками
.github/workflows/               сборка PyInstaller (см. «Релизы»)
```

Импорты внутри `src/` абсолютные от `src` (`from core.x import ...`), поэтому
запускать нужно `python src/app.py` или добавлять `src` в `sys.path`.

## Ключевые инварианты (не ломать)

1. **Потоки и Qt.** Фоновая работа идёт в обычных `threading.Thread`
   (Jira-запросы, автообновление, прогрев кэша). У таких потоков нет Qt event
   loop, поэтому `QTimer.singleShot(...)` из них **никогда не срабатывает**, а
   обращение к виджетам недопустимо. Результат в UI возвращается только через
   сигналы: `AppSignals.invoke_in_main_thread(callable)` в `app.py` и
   `IssueDialog.refresh_completed`. Обработчик pynput тоже работает в своём
   потоке и лишь эмитит `AppSignals.hotkey_triggered`.
2. **Запись файлов только через `atomic_write_json`** (tmp-файл → fsync →
   `os.replace`). Прямой `open(..., "w")` для конфигов не использовать.
3. **Нечитаемый конфиг не перезаписывать.** `CommentsRepository.load()` и
   `JiraSourcesRepository.load()` при ошибке парсинга переименовывают файл в
   `<имя>.broken-<метка>` и выставляют `load_warning`, который `Application`
   показывает при старте. Помните: `Application.cleanup()` сохраняет
   `comments.json` при выходе всегда.
4. **Токены Jira.** При включённой настройке `security.encrypt_tokens`
   (по умолчанию включена, флажок «Шифровать токены Jira») на диске —
   `dpapi:<base64>` (привязка к учётной записи Windows + энтропия
   приложения); при выключенной — открытый текст. В памяти и в UI — всегда
   открытый текст, это осознанно (нужен для `Authorization: Bearer`).
   `JiraSourcesRepository.load()` приводит файл к текущей настройке в обе
   стороны; переключение флажка в главном окне пересохраняет файл сразу.
   Недешифруемый токен обнуляется с предупреждением, файл при этом *не*
   считается битым и не пересохраняется.
   Токен не должен попадать в логи и URL (сейчас не попадает — сохранять это).
5. **Захват текста** построен на задержках и `GetClipboardSequenceNumber`;
   значения задержек в `ClipboardService` подобраны эмпирически — менять
   только с ручной проверкой в 1С.
6. **Порядок комментариев** = порядок в `comments.json`; drag&drop в таблице
   меняет только память, на диск попадает при «Сохранить» или выходе.

## Конфигурация во время работы

Каталог `%APPDATA%\1CCommentHotkeys\`: `config.json`, `comments.json`,
`jira_sources.json`, `jira_issues_cache.json`, `jira_last_issue.json`,
`app.log` (если включён флаг «Лог»). Формат каждого файла описан в `README.md`.

## Как проверять изменения

Автотестов нет. Что реально доступно:

- `python -m py_compile src/app.py src/core/*.py src/ui/*.py` и `pyflakes src`
  (единственное ожидаемое замечание — «`resources_rc` imported but unused»,
  импорт регистрирует Qt-ресурсы и нужен).
- Логику `core/*` можно гонять скриптами на любой ОС: репозитории принимают
  `config_dir`, `secret_store._call_dpapi` / `is_available` легко
  подменяются заглушками.
- UI и `Application` целиком можно поднять headless:
  `QT_QPA_PLATFORM=offscreen PYNPUT_BACKEND=dummy APPDATA=<tmp> python ...`
  (на Linux нужны `libgl1 libegl1 libxcb-cursor0 libxkbcommon-x11-0`).
  Dummy-бэкенд pynput бросает `NotImplementedError` при остановке listener —
  это артефакт окружения, а не баг.
- Настоящий DPAPI, глобальные хоткеи и вставка в 1С проверяются только на
  Windows руками. Для этого есть пре-релизная сборка (ниже).

## Сборка и релизы

Версия нигде в коде не хранится — источник версии только git-тег.
Оба workflow собирают одинаково: `windows-latest`, PyInstaller onefile со
спеком, генерируемым прямо в workflow (урезанный набор Qt DLL/плагинов, UPX),
результат `dist/1c-comment-hotkeys.exe`.

**Пре-релиз** — `.github/workflows/prerelease-pyinstaller.yml`
- Триггер: push в **любую** ветку (и ручной запуск).
- Публикует/пересоздаёт пре-релиз с тегом `v0.0` («Предрелизная сборка v0.0»),
  предыдущий `v0.0` удаляется. Это тестовая сборка последнего пуша; тег `v0.0`
  зарезервирован и никогда не используется для релиза.
- Проверить статус: `gh run list --branch <ветка>`, скачать:
  `https://github.com/iljyxa/1c-comment-hotkeys/releases/download/v0.0/1c-comment-hotkeys.exe`.

**Релиз** — `.github/workflows/release-pyinstaller.yml`
- Триггер: push тега `v*` (кроме `v0.0`) или ручной запуск.
- `softprops/action-gh-release` создаёт обычный GitHub Release с именем тега
  и прикладывает `.exe`; он становится `Latest`. Release notes автоматически
  не генерируются — заполняются вручную на GitHub.
- Процедура: слить ветку в `main` → `git tag vX.Y` → `git push origin vX.Y`.
  Существующие релизы: `v1.0`, `v1.1`, `v1.2`.

## Git и соглашения

- Основная ветка `main`; изменения делаются в feature-ветках
  (`feature/<тема>`), пуш ветки автоматически даёт пре-релизную сборку для
  ручного теста на Windows.
- Автор коммита — человек, который его делает (`user.name`/`user.email` из
  git-конфига участника).
- **Никакого авторства AI-ассистентов.** В коммитах, PR, коде, документации и
  release notes не должно быть трейлеров `Co-Authored-By: Claude ...`, строк
  «Generated with Claude Code» и любых упоминаний Claude/Anthropic (и других
  ассистентов). Перед пушем: `git log --format=%B main..HEAD | grep -i claude`
  должен быть пустым.
- Язык кода: имена — английские, докстринги, комментарии, логи и UI — русские.
  Стиль комментариев — по месту: пояснять *почему*, а не *что*.
- Документацию (`README.md`, `ARCHITECTURE.md`, этот файл) обновлять в том же
  коммите, что и поведение, которое она описывает.
