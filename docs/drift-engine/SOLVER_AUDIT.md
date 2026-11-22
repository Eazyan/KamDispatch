Дата: 22.11.2026 · Asia/Kamchatka. Версия: feature/kamchatka-drift-engine; исходный main d9893edcab8c26551cbd9bd57c3787abab207d80.

# Аудит решателей

| Решатель | Проверка источника / версии / лицензии | Реальный статус |
|---|---|---|
| WindNinja | [Исходники](https://github.com/firelab/windninja), VERSION 4.0.0; [лицензия](https://github.com/firelab/windninja/blob/master/LICENSE): US federal public domain + BSD для сторонних вкладов; учитывать LICENSE-3RD-PARTY | Нет host binary. Не собран и не запущен. Кандидат быстрого массово-согласованного поля ветра; его native conservation-of-mass и OpenFOAM momentum режимы различны |
| OpenFOAM | Реальный локальный opencfd/openfoam-default:2512 и образ kamcha-danil-openfoam:087118f, GPL-компоненты | simpleFoam успешно выполнен на двух сетках; журналы и поля сохранены. Это только воздух |
| driftScalarDyFoam | [Upstream](https://github.com/fightingxiaoxiao/driftScalarDyFoam), проверенный SHA 6dd2fe97fe12741aa18d317b46b596880d77702f; GPL-3.0-or-later в заголовке .C; upstream заявляет ESI v2012/v2106 | Сборка на доступном v2512 успешна (exit 0), реальный лог в solver-diagnostics. Учебный runtime на уменьшенной сетке успешно прошёл две стадии (5000/10000); это smoke без доказанной сходимости/массы. Совместимость с v2012/v2106 локально не проверена |
| snowBedFoam | [WSL GitLab](https://gitlabext.wsl.ch/atmospheric-models/snowbedfoam-v1-0), [статья авторов](https://doi.org/10.5194/gmd-15-6429-2022) | Найден Eulerian–Lagrangian проект; raw README недоступен через web-инструмент. Точная лицензия и версия ветки не подтверждены; не копирован, не собран |
| Alpine3D/SNOWPACK | [WSL Alpine3D](https://www.wsl.ch/en/services-produkte/alpine-3d/), [SNOWPACK](https://snowpack.slf.ch/): LGPL v3; SNOWPACK docs 3.7.0 | Источник/документация проверены, сборки нет. Кандидат эволюции свойств/слоёв снега, не готовая замена CFD города |
| SnowTran-3D | Нужны оригинальный дистрибутив автора и условия распространения; найденные публичные копии не доказывают права | Не интегрирован и не запущен; лицензия и поддерживаемая версия unknown |

Не найден отдельный shared/snow-analizer в проверенном checkout. Найдены SnowState,
CitySnow, API контракта аэродинамики, DSM, дорожный граф и сохранённая реализация Данила.
Переиспользованы SnowState, геоданные и постановка квартала. Не переписан city_sim.

## Команды RANS

Нужен локальный образ kamcha-danil-openfoam:087118f с /app/snowmap.foam и OpenFOAM v2512.
Он уже был установлен; не опубликован этим PR. Image ID и хеши — provenance.json.
На чистой машине этот образ автоматически недоступен; переносимый рецепт сборки пока
не подтверждён. Это ограничение воспроизводимости, а не успешная проверка чистого CFD CI.

```bash
docker run --rm --network none --cpus 2 --memory 3g -e HOME=/tmp -e PYTHONPATH=/app   --mount type=bind,src="$PWD/docs/drift-engine/cfd",dst=/audit   kamcha-danil-openfoam:087118f /audit/run_cfd.py dec-snow --mode coarse --iterations 360 --save-scalars
```

Быстрая проверка запуска: `--mode smoke --iterations 40`.
Контейнер пишет только в смонтированный каталог исследования. Быстрый CI не запускает CFD.
Для driftScalar клонировать указанный SHA, смонтировать readonly в /source, каталог
solver-diagnostics в /diagnostics и выполнить compile-driftScalar.sh в образе v2512.
Отдельный тяжёлый CI пока не настроен: нет проверенного переносимого образа связанного решателя.
