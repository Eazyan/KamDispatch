# Состояние текущего checkout
Updated: 2026-11-22 · Asia/Kamchatka (UTC+12)
branch: main

22 ноября 2026: пользователь явно поручил загрузить проект в Eazyan/KamDispatch, существующую ветку City_Sim. Это исключение назначения из GIT_INSTRUCTIONS.md для данного поручения. Подготовлен отдельный checkout /private/tmp/kamdispatch-city-sim-upload; проект размещён в city-sim/, существующий мониторинг сохранён. Публикационный набор: код, документация, runtime-данные; исключены out, локальные состояния/БД, ZIP, research sources/artifacts/cache и winter_audit/evidence. Проверка потенциальных токенов: совпадений нет. Core: 5 тестов; dynamic snow: 12 тестов успешно. Docker и полная suite не запускались. Текущий исходный checkout по-прежнему main/unborn без remote; публикация выполняется из отдельного checkout. На момент checkpoint push ещё ожидается; итог проверять по удалённой ветке.

sha: unborn

Git: нет коммитов, remotes и отслеживаемых файлов (`git ls-files` пуст). Накопленные исходники untracked до этой задачи. Git не менялся. Отдельная публикационная ветка из старой базы здесь не подтверждена; audit по указанному там пути отсутствует. PR #2 в SHKZhenya merged — проверен connector 22 ноября, это не публикация текущих файлов.

Активный трек WEATHER-001: источники ночных алертов найдены; дальше офлайн-аудит единиц/пропусков сохранённых CSV. Погодный атлас уже 298 точек; заново собирать платный API не нужно. DRIFT-001 доступна при явном выборе карты наносов. Критерии пользы/полевой точности открыты.

| Компонент | Проверка в этой сессии | Результат |
|---|---|---|
| Память | python3 scripts/agent_memory/verify.py; unittest test_memory | 10 isolated tests passed; итоговый verify — в VALIDATION.md |
| Маршрутизация | python3 -m unittest discover -s tests -p test_core.py -v | 5 passed |
| Синтетический снег | python3 -m unittest discover -s prototype -p test_simulate.py -v | 3 passed |
| Динамический backend | python -m unittest city_sim.tests.test_dynamic_snow -v | Системный Python: missing shapely; сохранённый venv — 12 passed |
| Погода | python -m weather_coverage --help | Системный Python: missing pyproj; сохранённый venv — OK |
| Hooks | .codex/hooks.json + direct smoke | UI integration not_verified: TUI SQLite code 14; trust не изменялся |

Сохранённое окружение: `/private/tmp/kam-snow-validation-venv/bin/python`. Оно временное, его наличие при следующем входе проверять; зависимости описаны в requirements-core.txt, requirements-city-sim.txt, weather_coverage/requirements.txt. README не является тестом. Полная city suite, Docker/браузер, внешняя модель и CI в этой сессии не запускались.

Научные/юридические unknown и различия сводов — в CLAIMS.md; очередь — TASKS.md. Существующие AGENT_HANDOFF и длинный свод сохранены как исторические источники. Вычислительный баланс массы не подтверждает уличную точность.
