# Журнал работы

Перед изменениями сервиса обращений прочитайте [журнал](docs/CITIZENS_LOG.md) и [контракт API](docs/CITIZENS_API.md).

Записывайте в журнал дату, изменения, принятые решения, результаты проверок и оставшиеся задачи. Сохраняйте контекст, необходимый следующему агенту для продолжения работы. Не включайте секреты и содержимое пользовательских обращений.


# KamDispatch development

Target repository: https://github.com/Eazyan/KamDispatch. Default integration branch: main.
For Kamchatka Drift Engine work only in feature/kamchatka-drift-engine. Never push main,
force-push, rewrite another contributor's history, or merge the PR yourself.
The former SHKZhenya/algorithms-kamchatka repository is a read-only source for this task.

Before edits inspect git status, remotes, HEAD, current origin/main and outstanding work.
Read docs/drift-engine/CHANGELOG.md, OPEN_QUESTIONS.md, VALIDATION.md and ARCHITECTURE.md.
Keep monitoring, citizen reports, city-map and the original city-sim modes compatible.
Follow city-sim/AGENTS.md for memory checkpoints when changing that component.

Every material decision or experiment must record date (Asia/Kamchatka), source,
verification status, code/data version, change and remaining checks. Preserve history.
Never label a slope closure as CFD or a scenario as observed snow. Mass balance does
not prove field accuracy. Keep expensive CFD outside interactive HTTP paths.

Quick checks: cd city-sim; python -m unittest city_sim.tests.test_drift_engine -v;
python -m city_sim.reproduce_drift_engine --output ../out/drift-engine.
Full regression requires the pinned Danil model; see .github/workflows/drift-engine.yml.
CFD reproduction and prerequisites: docs/drift-engine/SOLVER_AUDIT.md.
