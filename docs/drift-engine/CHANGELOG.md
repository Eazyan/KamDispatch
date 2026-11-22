Дата: 22.11.2026 · Asia/Kamchatka. Версия: feature/kamchatka-drift-engine; исходный main d9893edcab8c26551cbd9bd57c3787abab207d80.

# Журнал Drift Engine

22.11.2026 → возобновлена прерванная работа, проверены git status/remotes/SHA,
права Neverrie push=true и main d9893ed; gh auth status старого CLI сообщает invalid token,
но connector и git fetch работают → проверено инструментами → ветка feature/kamchatka-drift-engine →
включён новый main, сохранены оба раздела README при конфликте → PR/CI далее.

22.11.2026 → отдельный reduced drift engine, bounded compare API, экран сравнения,
21 эксперимент, mass invariants, полный regression suite после подключения pinned Danil →
код и логи → локально подтверждено → текущая ветка → старые режимы сохранены →
полная связанная CFD-модель и полевая валидация ещё не выполнены.

22.11.2026 → реально выполнены RANS smoke/coarse в ранее установленном образе →
cfd/results → подтверждено → OpenFOAM v2512 → поля скорости/давления/турбулентности →
проверить переносимый build образа и связь со снежной фазой.

22.11.2026 → чистое окружение: 86 city_sim + 5 core tests OK, pip check OK;
браузерный smoke нового сравнения пройден; Docker API build local-model успешен →
локальные команды → подтверждено → текущая ветка → все прежние runtime пути сохранены →
тяжёлый CI и полевая точность ещё не проверены.

22.11.2026 → driftScalarDyFoam 6dd2fe9: сборка v2512 и две стадии уменьшенного
upstream flatRoof3D успешно завершены → solver-diagnostics → execution smoke подтверждён →
OpenFOAM v2512 ARM64 → впервые проверен реальный snow scalar/dynamic mesh runtime →
нужны масса, сходимость и перенос на версионированную сцену Камчатки.

22.11.2026 → Docker API runtime и Nginx /drift-engine проверены, конфигурация
проксирования дополнена новым маршрутом → реальные контейнеры без сети → OK →
источники/хеши в provenance.json → исходный городской экран получил ссылку сравнения →
GitHub Actions проверяются после push.

22.11.2026 → по просьбе пользователя отделены выполненные шаги от незавершённых,
создан NEXT_CHAT.md с критериями продолжения → код/логи и повторный GitHub API
(PR #3 draft/open, CI 45004450310 success на 10911ed) → подтверждено → документационный
этап поверх 10911ed → исправлена устаревшая очередь сборки/браузера/CI → следующий
этап: настоящее поле ветра; полная CFD-модель и полевая валидация остаются открыты.

22.11.2026 · ФАКТ, проверено кодом/тестами: static RANS квартала подключён к SnowState через city_sim.cfd_wind; точная сетка 49×48, локальные east/south U и nominal AGL 0.778–1.151 m, хеши источников. 89 city_sim + 5 core OK; прежние 21 опыт mass error=0; парный RANS опыт relative mass error=1.801e-16. CI исходного 6b601f3: run 45005028332 success. Новый CI проверять после push. Рабочая ветка feature/kamchatka-drift-engine; origin/main d9893ed повторно fetch-проверен, изменений коллег при входе нет. Новый этап reduced 0.2; подробности docs/drift-engine/ARCHITECTURE.md и cfd/coupling-report.json. DRIFT-003 in_progress: дальше единая сцена и CFD снежная фаза с интегральным балансом, затем feedback. Городской HTTP остаётся slope baseline; offline ветер статичен, полевая точность unknown.

Первоначальный uniform-vector тест сравнивал со slope feedback и падал; исправлен тест прямой эквивалентности входов SnowState. Ошибки cwd при создании/запуске и обновлении отчёта исправлены явным рабочим каталогом. Новые Docker/browser не запускались: API/UI не менялись.

22.11.2026 · ПУБЛИКАЦИЯ ПРОВЕРЕНА: код 9c7a2e3c60923a67c96e2a3b12d6789d8e5498cd опубликован в Eazyan/KamDispatch feature/kamchatka-drift-engine. PR #3 draft/open, не merged; main d9893ed не изменён. GitHub Actions run 45005587147 success, включая 89 tests, core, baseline и новый static RANS coupling. Memory checkpoint/verify выполнены на этом кодовом SHA; финальный документационный коммит будет его потомком (снимок — проверенная историческая точка, не самоссылочный SHA). Далее единая сцена и CFD снежная фаза; DRIFT-003 остаётся in_progress.

22.11.2026 · ФАКТ/РАСЧЁТ: общая сцена квартала drift-scene-1 (49×48) экспортирована и подключена к cfd_wind. DSM, estimated ground, здания, снег и static RANS разделены; measured DTM и наблюдения unknown. Hash b5237a92e4695c7de892cb96cb688beb36886db4fc20843bba0b920f5cdc36f1. Начальный запас 646529.377514 kg; это initialization-only, не CFD snow budget. 91 city_sim + 5 core OK, пять целевых tests OK; прежний coupling результат сохранён. Источник/контракт: docs/drift-engine/SCENE.md, cfd/scene-v1. Следующий шаг DRIFT-003: Kamchatka driftScalar case с ограничением эрозии запасом и bed/air/boundary ledger; upstream stationary TEqn не содержит ddt. Новый CFD runtime не запускался. Вход: HEAD 421169a, main d9893ed, PR #3 draft/open, CI 45005772558 success; checkout был чист.

22.11.2026 · Внешнее изменение: PR #3 merged (09:10:03Z), не этим агентом. Main 123927c с внешними PR #4/#5 включён merge без конфликтов; изменения коллег сохранены. Код сцены 208075c, проверенный merge HEAD 819d2cf17d16ca32a06b58fcf0e5a282380562be. Продолжение: draft PR #6 https://github.com/Eazyan/KamDispatch/pull/6, не сливать. После merge 10 целевых tests OK; 91 city_sim + 5 core до merge. Документальный checkpoint — потомок проверенного SHA, CI нового head проверить отдельно. Далее Kamchatka CFD snow case, ограничение эрозии запасом и bed/air/boundary ledger.

23.11.2026 · ФАКТ: добавлен консервативный контракт bed/air обмена и полного
бюджета (`city_sim.cfd_snow_budget`), пять новых tests; полная suite 96 OK.
Общие доноры ограничены начальным запасом; пропущенный граничный поток обнаруживается.
Это подготовка адаптера, не CFD snow runtime. Далее transient уравнение, mapping
реальных объёмов/граней и интегральные boundary fluxes. [Контракт](CFD_SNOW_BUDGET.md).

23.11.2026 · ФАКТ/РАСЧЁТ: выполнен настоящий временной CFD снег на квартальной
геометрии (194581 cells), собственный kamchatkaSnowFoam v2512, пять сценариев по 5 s,
300 audited steps; max mass residual 3.074e-8 kg, массы неотрицательны. dt/2 даёт
0.355654% relative air L1; это чувствительность, не полная сходимость. Offline
replay добавлен в tests. [Постановка и ограничения](CFD_SNOW_CASE.md). Ветер/mesh
статичны, параметры E/D сценарные, полевая точность unknown. Далее длинный горизонт,
несколько dt/сеток, затем geometry/mesh/air feedback; переносимый тяжёлый CI открыт.
