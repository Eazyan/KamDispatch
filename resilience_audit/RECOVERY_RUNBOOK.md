# Запуск и восстановление локальных стендов

Нужны Docker Desktop с Compose, Python 3.12+ с tarfile data filter, OpenSSL, Git и patch. Сборке нужен доступ к npm, PyPI, Docker Hub и зеркалу решателя. Запуск с сохранёнными образами работает на внутренней сети. Внешняя отправка выключена. Все тома именуются префиксами локальных проектов.

## Основной стенд

Из корня отдельного checkout:

```bash
python3 resilience_audit/deploy/prepare.py
resilience_audit/deploy/compose build api city-api city-web
resilience_audit/deploy/compose up -d --no-build --wait --wait-timeout 180
resilience_audit/deploy/compose ps
curl --fail http://127.0.0.1:18088/healthz
curl --fail http://127.0.0.1:18088/api/v1/health
```

Панель доступна на [18088](http://127.0.0.1:18088/), City Sim через [тот же шлюз](http://127.0.0.1:18088/city/). В конфигурации отдельные PostgreSQL, SQLite, том медиа и синтетический ключ OpenWeather. Генерируемые материалы и бэкапы лежат под `.resilience-local`.

## Отдельный City Sim

```bash
python3 resilience_audit/deploy/prepare_standalone.py
cd .resilience-local/standalone/build
docker build -t kamdispatch-resilience-standalone-api:7d567eb -f Dockerfile.city-sim .
docker build -t kamdispatch-resilience-standalone-web:7d567eb -f Dockerfile.city-sim-web .
cd ../../..
docker compose -f resilience_audit/deploy/standalone.compose.yaml up -d --no-build --wait
curl --fail http://127.0.0.1:18089/api/v1/health
```

[Локальный City Sim 18089](http://127.0.0.1:18089/) использует отдельный SQLite.

## Проверка бэкапа

1. Убедиться, что проект называется `kamdispatch-resilience`, а шлюз слушает только loopback. F09 выгружает SQL, SQLite Online Backup и медиа в `.resilience-local/backup`.
2. Проверить SHA-256 дампов и хранить их вместе. Для согласованного снимка на время выгрузки остановить обработчик и исключить изменяющие операции. F09 выполнен с синтетикой.
3. F10 разворачивает временный проект с новыми томами, восстанавливает SQL, SQLite и JPEG, читает панель и API, снимает время и удаляет свои временные тома.
4. Для рабочего восстановления хранить комплект томов и OCI вне исходного хоста. Зафиксировать время последнего согласованного снимка и проверить права UID контейнеров при переносе.

```bash
python3 resilience_audit/tests/chaos.py F09_RESTORE
python3 resilience_audit/tests/cold_recovery.py
python3 resilience_audit/tests/snow_smoke.py
```

Для сверки используйте `python3 resilience_audit/tests/compare.py PUBLIC_KAMDISPATCH_ORIGIN PUBLIC_CITY_SIM_ORIGIN`. Сверка выполняет HTTP GET. Для остановки локальной копии используйте `resilience_audit/deploy/compose stop` и `docker compose -f resilience_audit/deploy/standalone.compose.yaml stop`. Тома остаются доступны для повторного старта.

Во время аудита точные Windows-образы отдельного City Sim сохранены локально в `.resilience-local/standalone/images.tar`. Рядом лежит локальный override `exact.compose.yaml` с идентификаторами образов и `linux/amd64`. Для повторного запуска именно этого варианта используйте `docker image load -i .resilience-local/standalone/images.tar`, затем добавьте второй флаг `-f .resilience-local/standalone/exact.compose.yaml` к команде запуска City Sim. Исходники и сборка из предыдущего раздела годятся для повторного построения без архива образов.

`resilience_audit/tests/regression.sh` запускает штатные тесты в отдельных контейнерах. Сценарии работы браузера доступны в `tests/browser.mjs`. Скрипту нужен отдельный Chromium с CDP на loopback:19271 и блокировкой внешней сети. Для проверки отчёта передайте `--report`, для самостоятельного City Sim — `--standalone`.
