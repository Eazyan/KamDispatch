# Проверки и состав результата
Updated: 2026-11-22 · Asia/Kamchatka (UTC+12)

| Проверка | Команда | Наблюдаемый результат |
|---|---|---|
| Память | `python3 -m unittest discover -s scripts/agent_memory -p test_memory.py -v` | 10 passed |
| Структура/ссылки/состояние | `python3 scripts/agent_memory/verify.py` после checkpoint | Два последовательных запуска: ok:true, errors:[]; файлы не изменились |
| Маршрутизация | `python3 -m unittest discover -s tests -p test_core.py -v` | 5 passed |
| Синтетический снег | `python3 -m unittest discover -s prototype -p test_simulate.py -v` | 3 passed |
| Динамика | `/private/tmp/kam-snow-validation-venv/bin/python -m unittest city_sim.tests.test_dynamic_snow -v` | 12 passed; библиотечные deprecation/ResourceWarning |
| Погодный CLI | `/private/tmp/kam-snow-validation-venv/bin/python -m weather_coverage --help` | exit 0, команды coordinates/demo/collect/map |
| Skills | bundled skill-creator quick_validate.py для обоих каталогов | Оба Skill is valid! |
| Hooks direct smoke | SessionStart/PreCompact/Stop скрипт и команды из JSON | 6/6 команд из JSON прошли из корня и city_sim без мутаций; лимит 2800 и stale проверены isolated tests; интеграция TUI отдельно |
| Реальный /hooks | `codex --no-daemon --no-alt-screen -C <root>` | not_verified: SQLite code 14 до /hooks, доверие не менялось |
| Сохранность материалов | Пересчёт SHA256 инвентаря | 5464 файла, 0 отсутствующих; единственное изменение после инвентаризации — запрошенное дополнение KNOWLEDGE_BASE.md; затем инвентарь обновлён |

Начальные ошибки сохранены честно: системный Python не импортировал shapely/pyproj. Переиспользовано найденное окружение; глобальные пакеты не менялись. Валидатор skill выявил YAML colon без кавычек, исправлено. Первые isolated memory tests обнаружили проблемы тестового fixture (канонизация /private пути и отсутствовавший VALIDATION.md); fixture и список required исправлены, итог — все десять успешны. Это не ошибки научного backend. Полная city suite, Docker/браузер, внешний решатель, live API и CI не запускались.

Демонстрация новой сессии без истории: isolated fixture получает только входные документы, очередь и пути источников, вызывает тот же selector. Результат — WEATHER-001, файл forecast_frames.csv, следующий офлайн-аудит. При явном DRIFT выбирается DRIFT-001 с командой backend unittest. Замена статуса WEATHER-001 на done/blocked исключает его из выбора; незавершённая зависимость не пропускается. Смена Git-ветки обнаруживается как stale. Несколько in_progress приводят к явной ошибке выбора. Повторный verify ничего не меняет. Это программная симуляция переноса, а не запуск второго LLM или импорт чужого чата.

Новые файлы (20):

- docs/agent-memory/INDEX.md, STATE.md, TASKS.md, DECISIONS.md, HANDOFF.md, LOG.md, CLAIMS.md, HOOKS.md, PROTOCOL.md, VALIDATION.md, inventory.csv, checkpoint.json.
- scripts/agent_memory/memory.py, inventory.py, verify.py, test_memory.py.
- .agents/skills/resume-kamchatka/SKILL.md, .agents/skills/checkpoint-kamchatka/SKILL.md.
- .codex/hooks.json, .codex/hooks/memory_hook.py.

Изменены (2): AGENTS.md и KNOWLEDGE_BASE.md. Исходный AGENT_HANDOFF.md, длинная база, исследования и production-код сохранены. Создание защищённых .agents/.codex прошло через sandbox approval; глобальные настройки и trust не менялись.

Переход: новая сессия → AGENTS + STATE/HANDOFF/TASKS → выбранный источник INDEX → конечная итерация → смысловой checkpoint + metadata snapshot + verify → новая сессия.

Примеры дальнейших действий: WEATHER-001 — проверить пропуски/единицы сохранённых прогнозного и архивного CSV без сети. DRIFT-001 — составить матрицу времени, геометрии, уборки и отрицательных контролей из существующих адресных случаев и плана валидации. Пороги, назначения, права СКДФ, уличная точность и расхождение публикационного checkout остаются в CLAIMS/TASKS на ручной проверке.

Git сохранён main/unborn, commit/push/PR не выполнялись по границе ТЗ. Для будущей передачи в SHKZhenya/main сначала проверить отдельный публикационный checkout и актуальный remote. Локальные файлы сохранены, но их нахождение в публичном репозитории не заявляется.
