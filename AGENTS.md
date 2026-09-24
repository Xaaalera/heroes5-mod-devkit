# Devkit agent instructions

## RU

- Перед работой читать README.md, docs/commands.md и CONTRIBUTING.md. Это общие инструменты, не репозиторий конкретного мода.
- `H5_WORKSPACE` — изменяемые файлы, `H5_GAME_DIR` — исходная установленная игра. Путь к соседнему инструменту вычислять от __file__, не от рабочей папки. Проверять работу в отдельной временной папке.
- Не запускать игру ради unit-тестов, не перехватывать общий курсор без согласованного интервала. Не считать отправленную команду подтверждением результата.
- Не менять чужие пакеты, штатные игровые бинарники или профили. Не выдавать sandbox за доказанную полную изоляцию профиля.
- Тесты в tests/ являются каноническими; потребитель может вызывать их через переходник. Код конкретного мода и его C++-тесты сюда не копировать.
- Соблюдать обязательное независимое ревью из CONTRIBUTING перед каждым push; отчёт нельзя подменять собственной оценкой. Запись аттестации не является доказательством личности рецензента.
- Сохранять RU/EN, ссылки на публичные исследования и явные границы проверки. Содержимое исходной игры никогда не публиковать.

## EN

- Read README, docs/commands and CONTRIBUTING. This repository owns shared tools, not individual mods.
- H5_WORKSPACE owns mutable state; H5_GAME_DIR points to the installed source. Resolve sibling tools from __file__, not workspace. Test a workspace outside this checkout.
- Unit tests never launch the game. Do not commandeer shared input without a coordinated interval. Dispatch does not prove completion.
- Preserve foreign packages, original binaries and profiles. Sandbox creation does not certify complete profile isolation.
- tests/ is canonical; consumers may bridge to it. Keep individual mod C++ code/tests outside the devkit.
- Independent pre-push review is mandatory; never invent verdicts. Attestation records judgments, not authenticated reviewer identity.
- Maintain bilingual docs, public research links and explicit validation limits. Never publish game contents.
