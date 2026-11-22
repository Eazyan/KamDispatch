# Поддержка и проверка hooks
Updated: 2026-11-22 · Asia/Kamchatka (UTC+12)

Локально найден `codex-cli 0.169.0-alpha.11.1`. `codex --help` содержит опцию hook trust, не использовавшуюся здесь. Текущая [официальная схема](https://learn.chatgpt.com/docs/hooks) допускает trusted-project .codex/hooks.json; [локальные skills](https://learn.chatgpt.com/docs/build-skills) — .agents/skills; [advanced config](https://learn.chatgpt.com/docs/config-file/config-advanced) проверена. Источник проверен 22 ноября 2026, это не гарантия поддержки каждым cloud/client режимом.

Созданы только SessionStart (startup/resume/compact), PreCompact и Stop. Команда вычисляет git root, поэтому работает из подкаталога. SessionStart читает только начала STATE/HANDOFF; additionalContext ограничен 2800 символами. PreCompact и Stop сравнивают metadata файлов и Git с явным checkpoint; выводят systemMessage только при изменениях. Семантические ошибки snapshot не определяет. Файлы с credentials/.env, кэши и symlink исключены. Скрипты не читают stdin/transcript/environment, не выходят в сеть и ничего не записывают. Stop не блокирует остановку и не создаёт повторных ходов. PostToolUse отсутствует.

Прямой smoke-test скрипта и конфигурационных команд описан в VALIDATION.md. Это **не** реальный lifecycle запуск. Попытка `codex --no-daemon --no-alt-screen -C <корень>` закончилась SQLite code 14, unable to open database file, до доступности /hooks. Сообщение клиента об «appears damaged» не доказывает повреждение БД: доступ ограничен sandbox; глобальную БД не исправляли. Статус `/hooks` и интеграции: **not_verified**.

Для ручного завершения в штатном терминале из корня:

```
codex --no-alt-screen
/hooks
```

Пользователь проверяет три команды и сам подтверждает точные определения hooks. Официальное правило: “Codex requires you to review and trust the exact hook definition.” Сам агент доверие не выставляет и review не обходит. Далее начать новую сессию, проверить SessionStart, выполнить обычную конечную итерацию и checkpoint, проверить PreCompact при ручном сокращении и Stop при завершении. При новом hash потребуется новый review. Отсутствие hooks не мешает входу через AGENTS и skills. OPS-002 остаётся blocked до этой проверки.
