# Общая сцена квартала v1

22.11.2026 · ФАКТ/РАСЧЁТ · source: сохранённые quarter.json, ground.npz,
wind.npz, summary.json и prepare.py. Код: city-sim/city_sim/drift_scene.py.
Статус: research_unvalidated. Новых данных и погодных запросов нет.

Сцена 49×48 используется адаптером `cfd_wind` и экспортируется для подготовки
снежной CFD постановки. Снимок: `cfd/scene-v1/scene.json`, `layers.npz`,
`initial-ledger.json`. Команда из city-sim:

```bash
python -m city_sim.drift_scene --output ../out/drift-engine/quarter-scene
```

`DriftScene.read()` проверяет SHA архива, версию и содержательный SHA сцены.
Последний включает метаданные и имена, shape, dtype, little-endian bytes всех слоёв.
Различие только ZIP timestamps не меняет содержательный идентификатор сцены.

| Слой | Единицы и смысл |
|---|---|
| ground_estimate_m | m относительно datum_egm2008_m; исходный грунт сохранённого CFD |
| dsm_egm2008_m | m EGM2008; bilinear на центрах CFD, reference-only |
| building_height_m | m над estimated ground; та же roof voxelization, что в RANS |
| buildings_mask | boolean; открытый снег на зданиях исключён |
| loose_kg_m2, bound_kg_m2 | kg/m² горизонтальной открытой площади; сценарий 5/0 |
| snow_surface_m | ground + loose/density; начальное состояние без bound снега |
| obstacle_surface_m | max(snow surface, ground + building height), без двойного DSM |
| wind_east_south_m_s | m/s; X east, -Y south; первый слой исходного RANS |
| wind_height_agl_m | m; номинальный центр terrain-following слоя |

Положение: локальная сферическая проекция, origin WGS84 southwest quarter.json,
заданные metres/degree, не UTM. Строки с севера на юг. Bounds — грани выбранных
CFD ячеек; они слегка отличаются от географического bbox квартала. Новый DSM слой
только интерполируется в пределах источника, экстраполяция запрещена.

Грунт не измеренный DTM: прежний prepare.py маскирует DSM возле footprints,
заполняет ближайшими немаскированными samples, затем сглаживает. Этот результат
переиспользован без новой обработки. DSM не прибавляется к высотам зданий.
`measured_dtm=null`, observations=[], статус unknown. Высоты части зданий оценочные;
лицензии и происхождение прежних входов — DATA_SOURCES.md. Новых прав на данные
этот экспорт не предполагает.

Начальный запас 646529.377514 kg, density 200 kg/m³, 2.5 cm сценарного рыхлого
снега на открытом грунте. Снег на крышах не моделируется. Нулевой air_initial_kg —
заданное начальное условие будущего опыта, не интеграл рассчитанного scalar T.
Начальный ledger не является временным или интегральным бюджетом CFD снежной фазы.
Изменение снегозапаса не пересчитывает статический RANS: это следующий этап.

Локально проверены 91 city_sim tests и 5 core, roundtrip снимка, обнаружение
изменённых archive/manifest, геометрия без двойного учёта зданий, hash sensitivity,
границы интерполяции. Прежний RANS/saltation опыт сохранил свои численные результаты.
Первая проверка старого теста ждала три source hashes; расширена до четырёх,
поскольку теперь явно учитывается ground.npz.

Следующий атомарный шаг: создать реальный Kamchatka driftScalar case из этой сцены,
ограничить эрозию запасом ложа и измерять bed/air/boundary/loss ledger. При чтении
pinned upstream 6dd2fe9 обнаружено: TEqn.H содержит stationary div/settling/diffusion,
без fvm::ddt; erosion в driftScalarDyFoam.C зависит от shear и не получает запас
сцены. Поэтому простой перенос tutorial и его exit 0 не докажут сохранение массы.
Это факт чтения кода, не результат нового runtime. Docker образ доступен локально;
новый CFD расчёт в этом этапе не выполнялся.

Городской HTTP использует другую область: контракт сцены квартала не закрывает
согласование городского масштаба или динамического mesh. DRIFT-003 in_progress.
