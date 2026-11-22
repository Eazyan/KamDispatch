Дата: 22.11.2026 · Asia/Kamchatka. Версия: feature/kamchatka-drift-engine; исходный main d9893edcab8c26551cbd9bd57c3787abab207d80.

# Решения (добавлять историю)

22.11.2026 → Eazyan/KamDispatch — новый основной репозиторий, старый только источник →
прямое ТЗ пользователя + GitHub permissions push=true аккаунта Neverrie → подтверждено →
main d9893ed / feature branch → сохранены Monitoring/Citizens/City_Sim → проверить PR/CI.

22.11.2026 → переиспользовать SnowState, не разрабатывать второй закон переноса →
snow_state.py + регрессии → численно проверено → drift-engine-reduced-0.1 → добавлены
отдельный режим/API/сравнение → нужно поле ветра и наблюдения.

22.11.2026 → уклонная поправка только как явно маркированный baseline → реализация и
21 опыт → research_unvalidated → текущая ветка → не называется CFD → WindNinja пока не подключён.

22.11.2026 → сохранить отдельный RANS квартала и реально повторить расчёт → прежняя
постановка + новые логи OpenFOAM v2512 → выполнено → текущая ветка → есть воздух на
двух сетках, но нет связанной снежной фазы → следующий этап совместимость driftScalar.

22.11.2026 · ФАКТ/ПЛАН · Источник: сохранённый RANS coarse, run_cfd.py и новые
VectorWindTests. Выбран существующий проверенный CFD вместо повторной установки
WindNinja. Подключение явно offline на точной CFD сетке; переносить в городской
HTTP другой bbox/CRS без согласования сцены нельзя. Сохранённый static RANS отделён
от slope default; повторного платного сбора погоды и повторного CFD запуска нет.
Ограничение: surface saltation с log-law u*, без взвеси и feedback на воздух.
Следующий этап — единая сцена и CFD снежная фаза с полным интегральным ledger.

22.11.2026 · ФАКТ/РАСЧЁТ: общая сцена квартала drift-scene-1 (49×48) экспортирована и подключена к cfd_wind. DSM, estimated ground, здания, снег и static RANS разделены; measured DTM и наблюдения unknown. Hash b5237a92e4695c7de892cb96cb688beb36886db4fc20843bba0b920f5cdc36f1. Начальный запас 646529.377514 kg; это initialization-only, не CFD snow budget. 91 city_sim + 5 core OK, пять целевых tests OK; прежний coupling результат сохранён. Источник/контракт: docs/drift-engine/SCENE.md, cfd/scene-v1. Следующий шаг DRIFT-003: Kamchatka driftScalar case с ограничением эрозии запасом и bed/air/boundary ledger; upstream stationary TEqn не содержит ddt. Новый CFD runtime не запускался. Вход: HEAD 421169a, main d9893ed, PR #3 draft/open, CI 45005772558 success; checkout был чист.
