# Алгоритмы Камчатки

Перед первой содержательной задачей прочитай [правила Git](GIT_INSTRUCTIONS.md). Назначение изменений — публичный `SHKZhenya/algorithms-kamchatka`, `main`; commit/push/PR выполняй в границах поручения, без отдельного разрешения не публикуй.

Вход в новую сессию: [STATE](docs/agent-memory/STATE.md), [HANDOFF](docs/agent-memory/HANDOFF.md), [TASKS](docs/agent-memory/TASKS.md), затем нужная строка [INDEX](docs/agent-memory/INDEX.md) и её первоисточники. [База знаний](KNOWLEDGE_BASE.md) — содержательный навигатор; большие отчёты читать по треку, не всю историю сразу.

Проверь реальные `git status --short --branch`, remote, SHA, код и тесты. Прошлый отчёт не доказывает сегодняшнее состояние checkout. Не удаляй исследования, код и чужие изменения; проверь возможную параллельную работу.

«Продолжай» означает последнюю активную незаблокированную задачу HANDOFF/TASKS. Для указанного трека выбирай только доступную todo/in_progress с выполненными зависимостями. Несколько активных задач — озвучь неоднозначность; done не возвращай без доказанной регрессии. Помощник: `python3 scripts/agent_memory/memory.py next --track WEATHER` (DRIFT для наносов).

Конечный цикл: LOAD → CHOOSE → PLAN → IMPLEMENT → TEST → CHECKPOINT → NEXT/BLOCKED/DONE. Максимум три попытки одной ошибки; никаких бесконечных фоновых циклов или автоматических платных запросов.

После значимой итерации обнови STATE/TASKS/HANDOFF/LOG; LOG дополняй, не переписывай. Новые выводы сохраняй с датой, источником и статусом проверки. Различай ФАКТ/МАТЕРИАЛ, СО СЛОВ, РАСЧЁТ, ГИПОТЕЗА, ПЛАН, НЕИЗВЕСТНО; неоднозначное — в ручную проверку, научные/юридические факты не исправлять догадками.

После содержательного checkpoint: `python3 scripts/agent_memory/memory.py checkpoint`, затем `python3 scripts/agent_memory/verify.py`. Локальные навыки: resume-kamchatka и checkpoint-kamchatka. Hooks вспомогательны и требуют ручного review; базовый вход работает без них. Полный [протокол](docs/agent-memory/PROTOCOL.md).
