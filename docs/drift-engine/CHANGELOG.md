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
