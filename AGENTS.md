# Devkit agent instructions


## Public projects / Публичные проекты

- [Deployment Preview / Предиктор](https://github.com/Xaaalera/heroes5-deployment-preview): native placement projections.
- [Bank Reference / Справочник армий](https://github.com/Xaaalera/heroes5-bank-reference): possible bank armies.
- [Mod Devkit / Девкит](https://github.com/Xaaalera/heroes5-mod-devkit): shared tools used to build and test both DLL mods.
- [Knowledge / База знаний](https://github.com/Xaaalera/heroes5-knowledge) · [public site / сайт](https://xaaalera.github.io/heroes5-knowledge/).
- [Author / Автор — Xaaalera](https://github.com/Xaaalera) · [email](mailto:dampirsimpl@gmail.com) · [personal Telegram / личный Telegram](https://t.me/Victima).
- [Heroes V Universe / Heroes Lobby](https://h5lobby.com/).

RU: эти проекты принадлежат автору; база знаний и моды не являются официальными продуктами Universe. Общие факты и контакты обновлять во всех канонических репозиториях. EN: These are the author's projects, not official Universe products. Keep shared facts and contact links consistent across canonical repositories.

## Owner priority — 2026-10-04 / Приоритет владельца

- RU: текущая задача — ускорить цикл разработки через native hot reload. После фиксации накопленных изменений исследовать и реализовать подхват добавленных/изменённых C++ DLL-функций, exports и hooks в том же работающем процессе игры. Проверять безопасное отключение старых callbacks/hooks, регистрацию новых функций и передачу состояния; сохранять PID/creation/ownership guards. Измерять полный цикл и этапы в JSON по требованиям мастерской. Lua/config reload и перезапуск карты — дополнительные возможности, не основная приёмка.
- EN: Current task is speeding development through native hot reload. After committing accumulated changes, investigate and implement added/changed DLL functions, exports and hooks in the same live game process. Verify old callback/hook teardown, new registration and state transfer; retain PID/creation/ownership guards. Measure complete cycles and phases in JSON. Lua/config reload and map restart are secondary capabilities, not primary acceptance.
- RU: один корневой SDK и ссылки потребителей; предиктор временно на паузе. Владелец сам остановил предыдущий прогон, повторять его сейчас не нужно. / EN: One top-level SDK and linked consumers; predictor work is paused. The owner stopped the previous campaign; do not restart it now.

## RU

- В связанной мастерской использовать один корневой checkout каждого репозитория. Вложенные зависимости модов — ссылки на этот SDK; не создавать независимые копии и не переносить правки вручную. Перед проверками из корня мастерской выполнить `scripts/sync-subrepos.ps1 -Check`. После коммита SDK синхронизировать gitlink потребителей и проверить их. Для самостоятельного клона сохраняется обычный закреплённый submodule.

- Пользователь запускает игру обычным способом через Heroes/Lobby. Наши моды подключаются через DLL; отдельный EXE для игрока запрещён владельцем. Диагностические EXE и Python-команды принадлежат только инструкциям разработчика. При смене поставки синхронизировать README, RU/EN wiki, devkit, инструкции агентам и release notes; прежние EXE-пакеты не публиковать.

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

- In a linked workshop, each repository has one top-level checkout and mod dependencies are junctions to this SDK. Never create independent copies or copy edits between consumers. Run the workshop's `scripts/sync-subrepos.ps1 -Check` before verification. After an SDK commit synchronize consumer pins and reverify them. Standalone clones retain ordinary pinned submodules.

- Players keep the ordinary Heroes/Lobby launch. Our mods load through DLLs; the owner rejects separate player launchers. Diagnostic EXEs and Python commands belong only in developer instructions. Delivery changes must update README, RU/EN wiki, devkit, agent instructions and release notes together; never publish the superseded EXE packages.

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
