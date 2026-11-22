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
