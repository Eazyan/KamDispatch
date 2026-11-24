# Контрольные суммы источника и работающих контейнеров

Снято 24.11.2026. Windows проверен через чтение метаданных Docker, SHA-256 внутри контейнеров и Git. Mac проверен аналогичными командами. HTTP-проверки отдельно зафиксированы в [http-parity.json](http-parity.json).

| Файл | Windows :8080 и изолированный Mac :18088 | Действующий Mac |
| --- | --- | --- |
| `monitoring/api.py` | `53f10f7ea85c733564835b40a698c21d385c1af1ecb83a13734164c891668847` | `89d1561a9e0f55f713d11e03452e92d2980525a34782dbf89ccf45dc94bdaa20` |
| `city_sim/api.py` | `e02952ffd3d1761f96632f13f3d9375bfdd99801f2ee8489550ae8b327c52b82` | `27ecea3e9401b84a1d19807b932cafecb54ad466b6f6e0b4457d385fff623784` |
| `city-map/map.js` | `87e1f3728f04c404ab6e7f5c3964f841952364b56597376a7bb4d02735dfc611` | `da23b3783ba4eade73a470d8f6433c3046baeadf2619d56f619cf75085184ddb` |

- Для первых двух файлов старый Mac совпадает после нормализации Windows CRLF → LF. Для карты отличаются и Git-ревизия, и переводы строк.
- Windows checkout базовых образов `1c3cdd82d29958242c1bcd91d861cf082b493c8f`, дерево `dff16ad5615c066d3798cac5c2e7c87373b4139f`.
- Windows checkout публичного bind mount и прокси `2bf45fc1e56a93af84559865540b90a2ac3d7f54`, дерево `adc5e525b4b7e1c8a78dcaa1ea398fedb45b3502`. Три файла карты совпали с GitHub `ec19fb51bd4d917987c4a37eeef3d3a107ace344` после нормализации CRLF. Остальное дерево соответствует `1c3cdd8`.
- Старый checkout Mac `ec19fb51bd4d917987c4a37eeef3d3a107ace344`. По HTTP файл карты старого Mac имеет байты, отличные от Windows. SHA текущего checkout сам по себе не подтверждает содержимое старых образов и bind mount.
- Windows OCI ID основного образа `dad5337b7ec7756bf79f0d820aa5cf552ce71451bd8d3b37b840ac7e7a3925fb`, City API `28b43d079f7d1806d04b61c7945973482dbbabfe10a46606564d93565e230636`. Сборка Mac использует ARM64 и другие OCI ID.
- Docker подтвердил `internal=true` у прикладной сети. Шлюз подключён к ней и к отдельной ingress-сети с привязкой порта к loopback.

## Отдельный City Sim на публичном 8008

- GitHub и Windows Git HEAD совпали на `7d567eb0a1732895f0e5443ee0e10bfe90f7b69d`. После игнорирования пробельных различий Windows checkout содержал изменения `city_sim/city_snow.py` и Compose. Патч модели, проверенный по байтам рабочего образа, сохранён в `windows-standalone.patch`.
- Хеши работающего API на Windows совпали с восстановленным исходником. `city_sim/api.py` — `e551a266971af425eda58c205f334826a3cb3898d3821f6c498c9d3873a77ac5`, `city_sim/city_snow.py` — `71a24da96e741287799b4cf551f4b0d6e9f068a53ff002c63ec3931d18e9e8d9`. Статические `index.html` и `map.js` совпали с HTTP.
- Рабочие Windows-образы экспортированы без тегов, загружены на Mac как linux/amd64. City Web — `3e6a7af76cf8e629db0fdc2352b5c3a9bc4999f1c5818094ed1ad098d37202c1`, City API — `b69d11f9837e43f72ee3afeef89a3f3dec17d081a4c88db4b3085101d036c97d`. Тома и секреты к образам не прилагались. Снимок лежит локально в `.resilience-local/standalone/images.tar`.
