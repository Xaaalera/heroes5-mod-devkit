# Devkit agent instructions

## RU

### Порядок работы и проверки

1. Прочитай README.md → docs/commands.md → CONTRIBUTING.md. Для исследования игры начни с [карты знаний](https://xaaalera.github.io/heroes5-knowledge/reference/research-index/) и указанного там опыта, а не угадывай адреса/сигнатуры.
2. До изменений запиши `git rev-parse HEAD` и `git status --short`. Различай каталог инструментов, H5_WORKSPACE и H5_GAME_DIR; не используй имя личной папки как настройку.
3. Для изолированных проверок в Python-среде выполни `python -m pip install -r requirements-dev.txt`, затем `python -c "import keystone, unicorn"` и `python -X utf8 -m unittest discover -s tests -v`. Проверка импорта обязательна: отсутствие эмулятора может превратить тесты в пропуски. После изменения review-инструментов также `npm ci` и `npm run check`; публикация проходит отдельное независимое ревью.
4. Эти тесты не требуют игры. При проверке игрового сценария используй только согласованный объём действий, зафиксированную сборку и проверяемую песочницу; чтение README или успешный deploy не равны результату в игре. После запуска различай dispatched и completed, проверяй фактическую фазу и закрытие.
5. Сверяй пути после resolve: Windows может вернуть короткое имя 8.3. Не импортируй Python из H5_WORKSPACE, профилей или полученных игровых файлов.
6. В итоговом отчёте укажи: репозиторий/ревизию; область проверки; команды и коды выхода; passed/failed/skipped; доказательства; что не запускалось/не проверено. Не скрывай пропуски и не называй эмуляцию живым игровым тестом.


- Перед работой читать README.md, docs/commands.md и CONTRIBUTING.md. Это общие инструменты, не репозиторий конкретного мода.
- `H5_WORKSPACE` — изменяемые файлы, `H5_GAME_DIR` — исходная установленная игра. Путь к соседнему инструменту вычислять от __file__, не от рабочей папки. Проверять работу в отдельной временной папке.
- Не запускать игру ради unit-тестов, не перехватывать общий курсор без согласованного интервала. Не считать отправленную команду подтверждением результата.
- Не менять чужие пакеты, штатные игровые бинарники или профили. Не выдавать sandbox за доказанную полную изоляцию профиля.
- Тесты в tests/ являются каноническими; потребитель может вызывать их через переходник. Код конкретного мода и его C++-тесты сюда не копировать.
- Соблюдать обязательное независимое ревью из CONTRIBUTING перед каждым push; отчёт нельзя подменять собственной оценкой. Запись аттестации не является доказательством личности рецензента.
- Сохранять RU/EN, ссылки на публичные исследования и явные границы проверки. Содержимое исходной игры никогда не публиковать.

## EN

### Work and verification sequence

1. Read README.md → docs/commands.md → CONTRIBUTING.md. For game research start from the [knowledge map](https://xaaalera.github.io/heroes5-knowledge/reference/research-index/) and its experiment records, not guessed addresses/signatures.
2. Record `git rev-parse HEAD` and `git status --short` before changes. Distinguish the tool checkout, H5_WORKSPACE and H5_GAME_DIR; personal directory names are not configuration.
3. For isolated checks in the Python environment run `python -m pip install -r requirements-dev.txt`, then `python -c "import keystone, unicorn"` and `python -X utf8 -m unittest discover -s tests -v`. Dependency import is required: a missing emulator can otherwise turn checks into skips. Review-tool changes also need `npm ci` and `npm run check`; publication requires separate independent review.
4. These tests need no game. For game scenarios stay within the authorized actions, pinned build and checked sandbox; reading docs or successful deployment is not in-game evidence. Distinguish dispatched/completed and verify actual phase and exit.
5. Resolve paths before comparing: Windows can return 8.3 aliases. Never import Python from H5_WORKSPACE, profiles or received game files.
6. Report repository/revision, scope, commands and exit codes, passed/failed/skipped, evidence, and what was not run or checked. Never conceal skips or present emulation as live gameplay.


- Read README, docs/commands and CONTRIBUTING. This repository owns shared tools, not individual mods.
- H5_WORKSPACE owns mutable state; H5_GAME_DIR points to the installed source. Resolve sibling tools from __file__, not workspace. Test a workspace outside this checkout.
- Unit tests never launch the game. Do not commandeer shared input without a coordinated interval. Dispatch does not prove completion.
- Preserve foreign packages, original binaries and profiles. Sandbox creation does not certify complete profile isolation.
- tests/ is canonical; consumers may bridge to it. Keep individual mod C++ code/tests outside the devkit.
- Independent pre-push review is mandatory; never invent verdicts. Attestation records judgments, not authenticated reviewer identity.
- Maintain bilingual docs, public research links and explicit validation limits. Never publish game contents.
