---
name: resume-kamchatka
description: "Продолжить Камчатку по сохранённой очереди: продолжай, что дальше, погодные алерты или снежные наносы."
---

Прочитай AGENTS.md, docs/agent-memory/STATE.md, HANDOFF.md, TASKS.md. Проверь git status, SHA, remote, чужие изменения и параллельную работу. Stale сначала разбери.

Погодные алерты → WEATHER; снежные наносы → DRIFT. Выполни `python3 scripts/agent_memory/memory.py next --track WEATHER` (или DRIFT). Без трека продолжи единственную in_progress; несколько активных задач — явная неоднозначность. Done/blocked не выбирать.

Открой нужную строку docs/agent-memory/INDEX.md и первоисточник выбранной задачи. Назови атомарный шаг, пути и команду проверки; выполни разрешённую конечную итерацию, затем checkpoint по PROTOCOL.md. Не пересказывай всю базу; не публикуй без разрешения.
