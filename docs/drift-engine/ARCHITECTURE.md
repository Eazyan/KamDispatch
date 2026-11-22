Дата: 22.11.2026 · Asia/Kamchatka. Версия: feature/kamchatka-drift-engine; исходный main d9893edcab8c26551cbd9bd57c3787abab207d80.

# Архитектура Drift Engine

Проверенный первый этап, не завершение полного ТЗ. Репозиторий Eazyan/KamDispatch,
интеграция только PR в main. История City_Sim (24d3307) объединена с актуальным main;
приложение обращений жителей и мониторинг сохранены. Исходный локальный SHA d7209bd
был создан прерванным предыдущим чатом от main 9cf90e4 и City_Sim. Новое состояние main
получено git fetch; конфликт README разрешён сохранением обоих разделов.

## Два уровня

1. `city-sim/city_sim/drift_engine.py`: консервативный SnowState + ограниченная
   направленная поправка скорости по уклону DSM и сценарной снежной поверхности.
   Это исследовательская параметризация ветра, НЕ WindNinja и НЕ CFD.
2. `cfd/run_cfd.py`: отдельный настоящий OpenFOAM simpleFoam RANS воздуха над кварталом,
   с рельефом и зданиями. Нет связанного CFD-снегопереноса и динамического снежного mesh.
   Результаты воздуха не подменяют снегоперенос и не загружаются молча в быстрый режим.

API `POST /api/v1/drift-engine/compare` ограничивает сетку 16/32/64, 1 час сценария,
скорость 40 м/с. Работает по сохранённым геоданным, не делает погодных запросов.
Возвращает оба массовых поля, ledger, solver/version, входной SHA256, время, provenance,
`research_unvalidated`, а подробный CFD-снег — `not_connected`. `legacy_dynamic` обозначает
SnowState, не эмпирическую карту CitySnow. Прежняя карта и API сохранены.
Экран `/drift-engine` использует общую шкалу кг/м² и сообщает границы доказательности.

## Воспроизведение

```bash
cd city-sim
python -m venv .venv
.venv/bin/pip install -r requirements-core.txt -r requirements-city-sim.txt
.venv/bin/python -m city_sim.reproduce_drift_engine --output ../out/drift-engine
.venv/bin/python -m city_sim
# http://127.0.0.1:8008/drift-engine
curl -H 'Content-Type: application/json' -d '{"resolution":32}' http://127.0.0.1:8008/api/v1/drift-engine/compare
```

Полные регрессии прежнего режима требуют закреплённого движка Данила;
его установка есть в `.github/workflows/drift-engine.yml` и README_CITY_SIM.
Запуск API нового режима не требует внешнего CFD. Точные команды CFD — SOLVER_AUDIT.
Следующее: полноценное поле ветра, единая сцена с DTM и высотами зданий,
связанный CFD-снег, независимые измерения. Никакой фоновой работы не настроено.
