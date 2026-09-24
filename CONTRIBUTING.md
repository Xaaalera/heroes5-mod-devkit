# Contribution and review / Изменения и ревью

## RU

- Один набор общих инструментов: исправления в scripts/ сопровождаются актуальным README или docs/commands.md и тестом значимого поведения. Конкретные моды живут отдельно; не добавлять сюда предиктор ради запуска общего инструмента.
- Документация двуязычная. Отделять проверку файлов/эмулятора от нового запуска игры, указывать сборку и ограничения. Историю исследований ведёт [публичный дневник](https://xaaalera.github.io/heroes5-knowledge/reference/research-diary/).
- Никогда не коммитить игру, PAK/H5M/H5U, извлечённые ресурсы, DLL/EXE, профили, сохранения, журналы или личные пути. Генератор читает ресурсы пользователя локально. Новые рабочие папки проверяются вне клона; не привязывать инструменты к имени личной директории.
- Перед push: выполнить `npm test`, `npm run check`, `npm run review:secrets`. Получить независимые read-only заключения craft, architecture, tests, docs, security по точному diff из `npm run review:info`.
- Оценка: 10 минус 20 за blocker, 3 за major, 1 за minor; нерешённые blocker/major запрещены. Docs-review содержит reviewer, все Markdown-пути и восемь критериев: purpose, structure, specificity, reproducibility, evidence, applicability, translations, maintenance. У каждого PASS/N/A есть обоснование; findings перечисляются явно. Не выдумывать оценки.
- Реальные результаты передать в `npm run review:attest -- <results.json>`, отдельно закоммитить `.review/attestations`, затем push. Изменение исходников требует свежего ревью. `.claude/review.config.json` фиксирует базу; менять её согласованно с проверенным диапазоном, не чтобы исключить непроверенные файлы.
- `npm ci` устанавливает pre-push hook. CI подтверждает тесты и запись ревью, не запускает модели или игру. Локальный hook технически обходится; серверная защита ветки отдельно не обещается.

## EN

Keep one canonical tool implementation. Changes to scripts update README/commands and meaningful behavior tests. Individual mods stay separate; generic tools must not require the predictor.

Maintain RU/EN parity. Distinguish static/emulated checks from fresh game acceptance, identify the build and limitations, and add significant research results to the public diary. Never commit game/resources, packages/binaries, profiles, saves, logs or personal paths. Exercise workspace paths outside the checkout.

Before push run test/check/secrets and obtain independent read-only craft, architecture, tests, docs and security reviews for the exact review:info diff. Score 10−20×blockers−3×majors−minors, with no unresolved blocker/major. Docs review identifies its reviewer, all changed Markdown files and reasoned PASS/N/A judgments for the eight criteria listed above, with explicit findings.

Record actual results through review:attest, commit the generated attestation separately, then push. Source changes require renewed review. Change the configured base only with the reviewed range, never to conceal unchecked work. npm ci installs the hook; CI checks tests and the attestation, not AI reviewers or the game. Local hooks are bypassable and do not imply server-side branch protection.
