# Карта источников
Updated: 2026-11-23 · Asia/Kamchatka (UTC+12)

Сначала STATE, HANDOFF, TASKS; далее только выбранный трек. Пути в Markdown относительны к файлу. Не читать inventory целиком в контекст.

| Тема | Первоисточники | Статус |
|---|---|---|
| City Sim: управление 3D-картой | [Код](../../city_sim/web/map.js), [Управление](../../README_CITY_SIM.md) | 23.11.2026: 87d9d78; два пальца горизонтально — поворот, вертикально — наклон; browser smoke OK |
| Погода / ночные алерты | [Атлас](../../weather_coverage/heatmaps/REPORT_HEATMAP_VIDEO_RU.md), [код и команды](../../weather_coverage/README.md), [возможности API](../../weather_coverage/API_CAPABILITIES.md), [вечерние гипотезы, §22/29](../KAMCHATKA_KNOWLEDGE_BASE.md) | 298 точек запросов; прогноз, не датчики; алерты — гипотеза |
| Drift Engine / новый репозиторий | [Продолжение Drift Engine](https://github.com/Eazyan/KamDispatch/blob/feature/kamchatka-drift-engine/docs/drift-engine/NEXT_CHAT.md), [Правила](../../GIT_INSTRUCTIONS.md), [код массового переноса](../../city_sim/snow_state.py) | Новый рабочий checkout /private/tmp/kamdispatch-city-sim-upload; docs/drift-engine там; связанная CFD-модель ещё не завершена |
| Снежные наносы | [Динамический backend](../../research/dynamic-backend-2026-11-22/REPORT.md), [API](../DYNAMIC_SNOW_API.md), [полевая проверка](../../research/reality-validation-2026-11-21/REPORT.md), [адреса](../../research/address-snow-2026-11-22/REPORT.md) | Вычислительные контроли есть; полевая точность not_testable |
| Граф / рельеф | [Граф](../../winter_audit/docs/GRAPH_AUDIT.md), [DSM](../../winter_audit/docs/RELIEF_AUDIT.md), [источники](../../winter_audit/docs/SOURCE_REGISTRY.md), [входы и фасады](../../research/mobility-backend-2026-11-22/REPORT.md) | OSM/DSM, неизвестные зимние ширины и двери |
| Маршрутизация | [Модель](../../MATHEMATICAL_MODEL.md), [эксперименты](../../winter_audit/output/experiments/experiment_report.md), [тесты](../../tests/test_core.py) | Сценарные проходы, не допуск в эксплуатацию |
| Контракты / ответственность | [Аудит](../../winter_audit/docs/CONTRACTS_AUDIT.md), [0903](../../research/procurement-0903-2026-11-22/REPORT.md), [реестр](../../research/procurement-0903-2026-11-22/objects.json) | Закупка/проект ≠ действующее назначение; геометрии приблизительны |
| Интервью / вечерние идеи | [Длинный свод v1.1, §21–31](../KAMCHATKA_KNOWLEDGE_BASE.md), [ссылки на чаты](../../KNOWLEDGE_BASE.md) | СО СЛОВ; имена и спорные трактовки требуют сверки; kam4.txt локально не найден |
| Трафик | [Гипотеза и контекст](../../KNOWLEDGE_BASE.md), [прототип](../../prototype/README.md) | Синтетические данные; эффект на реальном трафике неизвестен |
| Экономика | [Свод, §4/26](../KAMCHATKA_KNOWLEDGE_BASE.md), [стоимость/ёмкость](../../SAFETY_AND_CAPACITY.md), [расчётные итоги](../../winter_audit/output/experiments/experiment_report.md) | Сценарная стоимость ≠ сокращение бюджета |
| Происхождение / права | [Provenance](../DATA_PROVENANCE.md), [лицензии](../THIRD_PARTY_LICENSES.md), [новые источники](../../research/geodata-accessibility-2026-11-22/SOURCE_REPORT.md) | Владельцев/право публикации проверять у конкретного набора |

[Ревизия утверждений](CLAIMS.md) · [решения](DECISIONS.md) · [протокол](PROTOCOL.md) · [hooks](HOOKS.md) · [инвентарь](inventory.csv).
Инвентарь фиксирует путь, назначение, размер, формат, доступное число записей, SHA256 и unknown для неустановленного владельца. Он не разрешает публикацию данных. Полные архивы/медиа не загружаются в контекст; секреты, кэши, symlink исключены.
