# Проверка отказоустойчивости KamDispatch

Стенд использует отдельный Compose-проект, отдельные тома, закрытую сеть для приложений и шлюз только на `127.0.0.1:18190`. Фикстуры OpenWeather и РНИС используют локальный TLS через DNS-алиасы Docker. Пробки поступают из локального издателя. Камеры, внешняя рассылка, Telegram и MAX отключены. `/public/dashboard` на стенде идёт через фильтр публичной панели.

```sh
python3 -m venv .resilience-validation/venv
.resilience-validation/venv/bin/pip install httpx==0.28.1 aiohttp==3.14.0 matplotlib==3.10.9
python3 resilience_validation/prepare.py
resilience_validation/compose build api city-api
resilience_validation/compose up -d --no-build
.resilience-validation/venv/bin/python resilience_validation/experiment.py load citizens dispatch snow sources cascade
.resilience-validation/venv/bin/python resilience_validation/chart.py
.resilience-validation/venv/bin/python resilience_validation/recover.py
```

Параметр `--seconds` задаёт окно каждой ступени нагрузки. При проверке по разделам передайте имена сценариев, как в примере. Сборку и нагрузочные измерения лучше выполнять последовательно. Числа относятся к синтетическому трафику. Результат пишется в JSON с HTTP-кодами, пропускной способностью, p50/p95/p99 и сэмплами Docker CPU/RAM. Код 0 означает клиентский таймаут или ошибку соединения.

Восстановление использует текущий commit из `git archive HEAD`, отдельные имена образов и томов, сборку с `--no-cache --pull` и заново созданное локальное окружение. Из действующего *испытательного* проекта выгружаются PostgreSQL, весь том City Sim и тестовые медиа. Для выгрузки worker и City Sim кратковременно останавливаются в этом же проекте. Архивы остаются под игнорируемой папкой `.resilience-validation`, временные контейнеры и тома восстановления удаляются автоматически. Внешние реестры доступны на этапе сборки. После развёртывания проверяются количество обращений, SQLite, рельеф, решатель и выдача восстановленного JPEG.

Чтобы остановить испытательный стенд с сохранением томов, выполните `resilience_validation/compose stop`.
