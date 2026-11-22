# Kimi follow-up: repair and finish map package v3

## Причина follow-up

Первый worker создал `mapprep/prep/integrate_map_v3.py`, но завершился до запуска: `TEMPLATE` объявлен после top-level кода, который вызывает `TEMPLATE.replace`. Исправление через `%TEMP%` было отклонено sandbox. Эта задача намеренно ограничена ремонтом внутри проекта и завершением сборки.

## Область работы

- Прочитай `AGENTS.md`, последние записи `WORKLOG.md`, `mapprep/TASK_integrate_map_v3.md` и текущий скрипт.
- Измени только `mapprep/prep/integrate_map_v3.py`, новые outputs в `mapprep/out/`, новый run log в `logs/` и `WORKLOG.md`.
- Никаких записей за пределами корня проекта; не создавай helpers в `%TEMP%`/AppData. Для многострочных PowerShell команд используй PowerShell here-string `@' ... '@ | python -` либо непосредственную правку файла в проекте, не Bash `<<EOF`.
- Не перечитывай весь проект и данные целиком: опирайся на уже сделанную инвентаризацию в `mapprep/TASK_integrate_map_v3.md` и WORKLOG; читай только нужные участки скрипта и минимальные строки входов для починки.
- Не трогай алгоритм, backend/frontend, исходные графы/CSV, Git/GitHub; не коммить/пушить.

## Конкретные шаги

1. Перемести объявление `TEMPLATE` перед первым выполнением кода, которое обращается к нему. Сохрани HTML содержимое без изменений. Не используйте временные файлы вне проекта.
2. Запусти `python -m py_compile mapprep/prep/integrate_map_v3.py` и устрани ошибки.
3. Запусти `python mapprep/prep/integrate_map_v3.py` из корня. Если проверка обнаружит реальные конфликты или несоответствия, устрани детерминированно: конфликтующие high-confidence КСОДД значения не выбирать автоматически, а отнести в review.
4. Проверь все acceptance criteria в `mapprep/TASK_integrate_map_v3.md`; зафиксируй команды, числа и ограничения в отчёте. Исходный `graph_v3.json` должен сохранить SHA-256 `c29d4675e8f230015cde370b0d6e13b9d13f7a93f65516bf7f889c34b8162c76`.
5. Обнови WORKLOG: follow-up, результат/блокер и один следующий шаг.

## Итог

Сообщи точные пути, проверки и pass/fail, counts high vs review, hash графа, ограничения и следующий шаг. Не говори «готово», если сборка/проверки не прошли.
