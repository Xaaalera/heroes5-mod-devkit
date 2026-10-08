# Devkit agent instructions

## Documentation ownership and article critique / Документация и критика статей

RU: Единственный источник живой документации — [наш сайт](https://xaaalera.github.io/heroes5-knowledge/), исходники статей — knowledge/docs. В репозиториях оставляем краткий README с назначением и ссылками, AGENTS с рабочими правилами и обязательные лицензии; руководства, справочники и объяснения не копируем по репам. Исторические исследования и приватные журналы сохраняем отдельно, не выдаём за текущую инструкцию. Изменение поведения сопровождается обновлением соответствующей статьи.
EN: The website is the sole source of living human documentation, authored in knowledge/docs. Repositories retain concise README entry points, AGENTS working rules and required notices; manuals, references and explanations link to the site instead of maintaining parallel copies. Preserve historical/private evidence separately and update the canonical article when behavior changes.

RU: Каждая новая или изменённая статья проходит пять независимых критиков по [методу critique](https://github.com/Xaaalera/claude-skills/blob/main/plugins/critique/skills/critique/SKILL.md): понятность новичку, техническая точность, воспроизводимость, структура/навигация, терминология/перевод. Каждый критик указывает место, конкретную проблему, последствия, серьёзность и доказательство; автор не выступает своим критиком. Проверяем факты по коду и наблюдениям, исправляем подтверждённые существенные ошибки, не придумываем оценки. До трёх раундов на статью; фиксируем хеш проверенного текста и решения по замечаниям. Подробный стандарт — [на сайте](https://xaaalera.github.io/heroes5-knowledge/contributing/).
EN: Every new or changed article receives five independent critiques: newcomer clarity, technical accuracy, reproducible steps, structure/navigation, terminology/translation. Findings identify location, failure, consequence, severity and evidence; the author is not a critic. Verify claims against code/observations, fix confirmed substantial defects, record the reviewed content hash and finding disposition, and cap review at three rounds. Follow the canonical site authoring standard; never invent scores.


## Problem-solving order

Test storage: reuse a sandbox; keep at most three prepared installations per storage area. Fresh copies need a concrete isolation hypothesis. Use GameAssets and `xkit storage clean`; immutable assets share a private cache, original installation and mutable files remain independent. Snapshot and verify before retiring a closed owned copy; retain reports and cache references. Keep two raw dumps, losslessly archive older ones. Test ownership/reparse/space/locking guards before destructive storage changes.

For every problem, first search our logs, research, backlog and handoff for prior occurrences, attempts, solutions and their verified conditions. Then research the web when needed for causes, documentation, existing solutions and libraries. Only then choose an approach and act. Repeat known experiments only for a new hypothesis or changed conditions. Record links, conclusions and verification limits in the existing log.

RU/EN, 2026-10-06 correction: earlier player PASS used native-probe.launch with developer import/startup-script patches even with control=False. Corrected plugin-player-check now uses ordinary flags0 CreateProcessW, retains the owned handle before capturing creation identity, and preserves staged files if game exit is unconfirmed. Stock -advmap is a request, not proof of map identity. Use the current paired-graphics acceptance report; do not claim unmodified startup from those old reports.

RU, 2026-10-06: каждый нативный мод выпускается отдельной DLL. Текущие пакеты используют общие dinput8.dll и d3d9.dll; исходный d3d9.dll игры сохраняется локально как d3d9.universe.dll и не распространяется. Проверены обычный запуск двух пакетов и независимое отключение. Bootstrap и графическая цепочка относятся к запуску; HMR ядра и плагинов проверяется отдельно. Инструкция установки — в README девкита; публикация ещё не завершена.
EN: Each native mod is a separate DLL; current packages share input/graphics infrastructure and retain the original graphics DLL locally. Ordinary two-package startup and independent stop are verified. Startup bootstrap/facade remain separate from hot core/plugin generations. Follow current devkit installation instructions; publication remains pending.


## Independent mod delivery / Независимая поставка модов

RU: Каждый мод выпускается и устанавливается отдельно: ресурсный — собственный H5U, нативный — собственная DLL. Общий загрузчик или инфраструктура SDK допустимы как описанная зависимость. Установка одного мода не требует остальных. Проверять отдельные пакеты и совместную установку. Загрузка и выгрузка по требованию во время игры требуют отдельной проверки.
EN: Release each resource mod as its own H5U and each native plugin as its own DLL. Do not merge several mods into one mandatory payload DLL. A shared loader or SDK infrastructure may be an explicit dependency. Installing one mod must not require the other mods. Verify separate packages and coexistence. Optional installation does not establish runtime demand loading/unloading; verify that capability separately.


[Game API](https://github.com/Xaaalera/heroes5-game-api) — shared C++ game bindings / общая библиотека привязок к игре.

2026-10-04 final prototype acceptance: ABI3 watch/functions/UI/window and validated engine CALL observer, state/errors/concurrent reload and same-source player package all verified. Native4/Python51 PASS; repeat scripts/plugin-check.py --live after changing runtime behavior. Read current commands first; historical dated failures are retained. Do not resume predictor work automatically. Uncommitted/unpublished prototype, with explicit limits on arbitrary prologues/multithread hooks/crash recovery/schema migration.

RU: native watch/release prototype ABI3: package bin/Heroes5Mods/Plugins/<name>.dll + общий bin/dinput8.dll; тот же payload source для live и выпуска. Существующие legacy-моды не становятся reloadable автоматически. EN: Read current docs/commands.md release notes; player startup stays ordinary DLL bootstrap, no developer client/Python dependency. Window-event/UI controls passed; arbitrary engine detours/concurrency hardening remain. Uncommitted/unpublished work.


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
- Пользователь разрешает фокус, мышь и клавиатуру для авторизованной разработки и проверки SDK без повторного согласования. По сообщению пользователя о необходимости фокуса или ввода немедленно прекратить их использование до сообщения о продолжении. Проверять только собственную тестовую игру; фоновый ввод не выдавать за физическую проверку.
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
- Unit tests never launch the game. The user authorizes focus, mouse and keyboard use in the owned test game without repeated coordination. Stop immediately when the user says they need focus or input; resume when they say to continue. Dispatch does not prove completion.
- Preserve foreign packages, original binaries and profiles. Sandbox creation does not certify complete profile isolation.
- tests/ is canonical; consumers may bridge to it. Keep individual mod C++ code/tests outside the devkit.
- Independent pre-push review is mandatory; never invent verdicts. Attestation records judgments, not authenticated reviewer identity.
- Maintain bilingual docs, public research links and explicit validation limits. Never publish game contents.

## Standalone use / Работа вне мастерской

RU: этот репозиторий можно использовать отдельно. Начни с его README и AGENTS.md; глобальная папка мастерской не обязательна. Если есть .gitmodules, выполни `git submodule update --init` после клонирования. В связанной мастерской используй её sync-subrepos вместо создания вторых checkout.
EN: This repository can be used independently. Start with its README and AGENTS.md; the global workshop is optional. If .gitmodules exists, initialize pinned dependencies with `git submodule update --init`. In a linked workshop use its canonical dependency synchronization.

- [Devkit commands / команды SDK](https://xaaalera.github.io/heroes5-knowledge/reference/xkit-commands/).
- [Game API contracts / контракты библиотеки](https://xaaalera.github.io/heroes5-knowledge/reference/game-api/).
- [Research index / карта исследований](https://xaaalera.github.io/heroes5-knowledge/reference/research-index/).

## Code standards / Стандарты кода

RU: перед новой правкой применяй подходящие установленные скиллы из [маркетплейса автора](https://github.com/Xaaalera/claude-skills). Имена переменных/параметров должны объяснять смысл; не использовать непрозрачные сокращения. C++ сохраняет calling convention, lifetime и ABI; Python использует описательные snake_case имена. Обязательные имена API/protocol/register и общепринятые PID/DLL/ABI сокращения допустимы. JS правила не переносить механически на C++/Python.
EN: Load the applicable guides before coding/reviewing. Use descriptive names, small functions with one responsibility, canonical dependencies and no speculative abstractions. Preserve native ABI/protocol compatibility during readability changes. Existing code is changed when relevant, not mass-renamed by this policy.

- All code: [solid](https://github.com/Xaaalera/claude-skills/blob/main/plugins/meta/skills/solid/SKILL.md), [ockham](https://github.com/Xaaalera/claude-skills/blob/main/plugins/meta/skills/ockham/SKILL.md).
- JS/TS only: [conventions](https://github.com/Xaaalera/claude-skills/blob/main/plugins/frontend-js/skills/conventions/SKILL.md).
- Tests: Codex alias `tests-architecture`, upstream [tests:architecture](https://github.com/Xaaalera/claude-skills/blob/main/plugins/tests/skills/architecture/SKILL.md).
- Documents: [standard](https://github.com/Xaaalera/claude-skills/blob/main/plugins/docs/skills/standard/SKILL.md), [lean-writing](https://github.com/Xaaalera/claude-skills/blob/main/plugins/meta/skills/lean-writing/SKILL.md), [wittgenstein](https://github.com/Xaaalera/claude-skills/blob/main/plugins/meta/skills/wittgenstein/SKILL.md).
- Changed user-facing UI: [ui-strings](https://github.com/Xaaalera/claude-skills/blob/main/plugins/i18n/skills/ui-strings/SKILL.md), [responsive-layout](https://github.com/Xaaalera/claude-skills/blob/main/plugins/frontend-css/skills/responsive-layout/SKILL.md).
- New public JSON error boundaries: [format](https://github.com/Xaaalera/claude-skills/blob/main/plugins/error/skills/format/SKILL.md); version changes explicitly, do not silently reinterpret native status words.
- Reviewers load every applicable guide listed in .claude/review.config.json. If a guide is not installed, read the canonical source above and report availability honestly. Do not vendor independent copies of these standards.

## Human-usable functionality / Использование человеком

RU/EN: Never expose raw memory addresses, pointer/structure byte offsets or address-derived labels in human docs/help/descriptions. Use meaningful function/event/type names and explain role. Numeric bindings belong in code/private machine logs. Reviewers reject address-only explanations; preserve dated facts and private originals when correcting older text.

RU/EN: use maintained libraries/standard language facilities for infrastructure before adding custom implementations. SDK logs use structlog + Python logging + concurrent-log-handler; project adapters only attach context and compiler locations. Event JSONL rotates at10MiB with five gzip backups; every process must use the same retention settings. Reports, dumps and raw diagnostics are retained separately. Parallel operations must retain session/operation/plugin/instance/stage/level/time, readable console output, machine-readable JSONL and linked raw diagnostics. Apply the same pattern to all SDK operations; do not claim complete adoption from CLI/watcher-only coverage.

RU: весь функционал проекта должен быть пригоден для самостоятельного использования человеком без AI. Основной сценарий требует понятного входа, справки, разумных настроек по умолчанию, видимого состояния и ошибок с действием для исправления. Цепочка внутренних Python/PowerShell/RPC команд не заменяет пользовательский интерфейс. Разработчик должен уметь подготовить окружение, создать/запустить/обновить плагин и получить готовый мод по документации самостоятельно.
EN: Every feature must be usable by a person without an AI agent. Provide a clear entry point, help, sensible defaults, observable progress and actionable errors. Internal scripts/RPC sequences may support diagnostics but do not satisfy the main user workflow. Acceptance includes following the documented workflow as a human; never document a planned friendly command as already implemented. This is a project rule, not a new skill.

RU: правило также относится к README, документации, справке, описаниям, примерам и сообщениям. Писать для указанной аудитории простым языком: зачем функция нужна, как начать, какой результат ожидается, как исправить ошибку. Объяснять термины при первом использовании; внутренние механизмы выносить в документацию разработчика. Инструкция не должна требовать AI для расшифровки или поиска пропущенных шагов.
EN: Apply the same rule to README, documentation, help, descriptions, examples and messages. Explain purpose, starting steps, expected result and recovery in language appropriate to the reader. Define unfamiliar terms on first use; keep internals in developer documentation. A person must be able to follow the instructions without AI filling missing steps.
