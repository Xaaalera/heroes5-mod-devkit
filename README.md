# xkit

RU, 2026-10-07: проверены замена ядра и двух плагинов в одном процессе, новые функции и экспорт ядра, сохранение состояния, отключение старых callbacks и откат. Два отдельных DLL-пакета из тех же исходников автоматически подключаются при обычном запуске игры. В консоли проверены Ctrl+Enter, история, Tab, изменение размера мышью и прокрутка логов без зума камеры. Причины ошибок подключения сохраняются; публикация текущей версии ещё не завершена.
EN: Live checks verify core/two-plugin replacement, added functions/core export, state transfer, callback teardown and rollback. Separate same-source DLL packages initialize during ordinary startup. Console checks cover Ctrl+Enter, history, Tab, mouse resizing and log scrolling without camera zoom. Startup failure reasons are retained; publication of the current version remains open.

**Xaaalera Toolkit SDK** — инструменты разработки модов Heroes V Universe / tools for Heroes V Universe mod development.

RU: Руководство описывает текущую рабочую версию. Новые возможности ещё не опубликованы; публичный клон может отставать.
EN: This guide describes the current working version. New features are not released yet; a public clone may lag behind.

## xkit: команда `xkit` / command `xkit`

RU: нативный ZIP содержит отдельную DLL плагина, общий `bin/dinput8.dll` для подключения модов и `bin/d3d9.dll` для графической цепочки. Перед первой установкой сохрани исходный `d3d9.dll` игры под именем `d3d9.universe.dll`; он не входит в архив и нужен для запуска. Пакеты одной версии SDK используют общие файлы совместно. Следуй README.txt внутри пакета. H5U устанавливается отдельным файлом в UserMODs.
EN: Each native ZIP contains one plugin DLL, shared input bootstrap and graphics facade. Before first installation retain the game's original d3d9.dll as d3d9.universe.dll; the original is required locally and is never redistributed. Same-version packages share infrastructure. Follow the package README.txt. Resource mods remain separate H5U files.

RU: короткий человеческий вход в SDK на Typer. Установка использует готовый uv; Python-команды и внутренние флаги в ежедневной работе не нужны. EN: Human CLI backed by Typer, installed from this canonical checkout in an isolated uv tool environment. Editable installation keeps one source of truth.

Установи [uv](https://docs.astral.sh/uv/getting-started/installation/) один раз. Для отдельной установки скачай SDK вместе с библиотекой Game API:

RU: Полный подготовленный архив `xkit-sdk.zip` уже содержит исходники, Game API и готовую папку `runtime`. Распакуй его, открой папку `xkit` и выполни команды установки ниже. Git-зависимости из такого архива отдельно скачивать не нужно. Текущая версия ещё не опубликована; разработчик SDK собирает этот архив командой `xkit sdk release`.

EN: A complete `xkit-sdk.zip` includes sources, Game API and the ready runtime. Extract it, open `xkit`, and run the installation commands below. Its dependencies do not need a separate Git download. The current version is unpublished; SDK maintainers generate this archive with `xkit sdk release`.

Альтернатива архиву — клонирование Git / Alternative to the archive — Git clone:

```powershell
git clone --recurse-submodules https://github.com/Xaaalera/heroes5-mod-devkit.git
cd heroes5-mod-devkit
```

RU: если репозиторий уже скачан отдельно без зависимостей, выполни `git submodule update --init game-api`. В связанной мастерской зависимости подготавливает её команда синхронизации; второй SDK не нужен.
EN: Standalone clones must initialize the pinned Game API dependency before native development. Linked workshops keep their existing canonical dependency setup.

В папке SDK:

```powershell
uv tool install --editable .
uv tool update-shell
```

Открой новый терминал. / Open a new terminal.

### Место на диске / Disk space

RU: SDK хранит большие неизменяемые ресурсы в отдельном общем кэше. Тестовые установки используют ссылки на этот кэш; оригинальная игра остаётся отдельной. DLL, карты, профили и изменённые ресурсы копируются независимо. Общие архивы защищены от записи: новые ресурсы мода создавай в его исходниках и выпускай H5U.

RU: В одной области хранения остаются максимум три подготовленные тестовые установки. Перед созданием следующей SDK архивирует старые закрытые копии. Отчёты сохраняются; изменяемые файлы и ссылки на общие ресурсы записываются в снимок. Кэш нужен для восстановления снимков. Проверка свободного места выполняется до копирования.

```powershell
xkit storage status
xkit storage clean
xkit storage restore .local/test-state/my-check/workspace/.local/test-state/retired-game.zip
```

EN: Large immutable resources share a private read-only cache. Original game files are never hardlinked; DLLs, maps, profiles and changed assets remain independent. Each storage area keeps at most three prepared test installations. Closed older copies are archived before removal; reports and mutable files remain available. Snapshots reference verified cache resources, so retain the cache. Use storage status/clean/restore; preparation checks free space before copying.

### 1. Настройка / Setup

```powershell
xkit setup
xkit doctor
```

RU: `setup` запоминает папки игры и проектов. `doctor` проверяет инструменты. Для нативных модов нужны Visual Studio C++ Build Tools и CMake; SDK автоматически выбирает компилятор x86 для Heroes V.
EN: Setup remembers game/project folders; doctor checks tools. Native mods need Visual Studio C++ Build Tools and CMake. SDK selects the x86 compiler automatically.

### 2. Создание и правка / Create and edit

```powershell
xkit new my-plugin
xkit start my-plugin
```

RU: Открой созданный `plugins/my-plugin/plugin.cpp`, измени `DisplayValue` и сохрани файл. Дождись сообщения об обновлении: число в игре изменится. Кнопка «Логи» открывает диагностику SDK, команды и список модулей. Во время разработки оставляй терминал с `start` открытым.
EN: Edit DisplayValue in the generated plugin.cpp, save and wait for the update message. The game display refreshes. Logs opens SDK diagnostics, commands and modules. Keep the start terminal open during development.

### 3. Остановка и выпуск / Stop and release

RU: `xkit game restart WorkshopPolygon` загружает указанную карту заново; `xkit game menu` возвращает в главное меню. Имя карты для рестарта обязательно. В консоли SDK: `restart WorkshopPolygon` и `menu`.
EN: xkit game restart WorkshopPolygon reloads that named map; xkit game menu returns to the main menu. Restart requires an explicit name. The console shares restart/menu.

RU: Пока сеанс SDK открыт, `xkit game map WorkshopPolygon` запрашивает загрузку карты в этой игре. Имена файлов из Maps дополняются по Tab; справка — `xkit game map --help`. В консоли SDK используется та же команда `map WorkshopPolygon`.
EN: While the SDK session is running, use `xkit game map WorkshopPolygon` to request that map. Tab completes sandbox map filenames; `xkit game map --help` explains the command. The embedded console shares it as `map WorkshopPolygon`.

RU: Нажми **Ctrl+C** в терминале с `start` и дождись подтверждения закрытия тестовой игры. Затем:
EN: Press **Ctrl+C** in the start terminal and wait for confirmed game closure. Then:

```powershell
xkit build my-plugin
xkit release my-plugin
```

RU: Команда покажет папку выпуска. Внутри нативного ZIP есть `README.txt` с установкой и удалением мода. Установи папку `bin` из архива в папку игры. Игрок запускает Heroes/Lobby обычным способом; SDK ему не нужен.
EN: The command prints the release folder. The native ZIP includes `README.txt` with installation/removal steps. Extract its `bin` folder into the game; the player uses ordinary Heroes/Lobby startup and needs no SDK.

### Как расширить C++ плагин / Extending a C++ plugin

RU: После `xkit new my-plugin` открой `plugins/my-plugin/plugin.cpp`. `DisplayValue` — число примера; `Dispatch` обрабатывает команды. Добавляй свои функции в `.cpp` и объявления в `.hpp` внутри этой папки: сборщик подхватывает новые файлы. `xkit start my-plugin` следит за сохранениями; ошибка компиляции оставляет прежнюю DLL работающей. Исправь ошибку и сохрани файл снова. `xkit release my-plugin` собирает отдельный ZIP мода из этих же исходников.

EN: Open the generated `plugin.cpp`. `DisplayValue` is the demo number; `Dispatch` handles commands. Add functions in `.cpp` and declarations in `.hpp` inside the project folder; the build discovers new files. `xkit start my-plugin` watches saves. A compiler error preserves the working DLL; fix and save again. Release builds an independent mod ZIP from the same sources.

RU: Сохраняемые данные находятся в `State`, а не в глобальных переменных DLL. SDK передаёт их через аргумент `memory` при вызове `Dispatch`. При HMR размер и `stateSchema` должны совпадать: изменение структуры отклоняется, автоматического переноса между разными схемами пока нет. В `State` допустимы простые значения; указатели, handles, строки C++ и другие владеющие объекты туда не помещай. `Dispatch` возвращает `1` при успехе; при отказе изменения сохранённого состояния не принимаются. Это не отменяет уже совершённые игровые действия.

EN: Persistent data belongs in `State`, supplied through `Dispatch`'s `memory` argument. HMR requires the same size and `stateSchema`; automatic migration between schemas is not implemented. Store plain values, not pointers, handles, C++ strings or other owning objects. Return `1` to accept a command. Rejection preserves saved state but does not undo external game effects.

RU: Именованные поля `PluginApi` в примере объясняют регистрацию: `dispatch` — обработчик, `eventCommand` — команда обновления интерфейса, `showEventResult` — показ результата. Для поддерживаемого наблюдателя игровых скриптов подключи `h5/hooks.hpp` и укажи:

EN: The starter's named `PluginApi` fields identify its handler and display event. To register the supported game-script observer, include `h5/hooks.hpp` and use these descriptor fields:

```cpp
.engineCallSite = h5::hooks::ScriptDispatchCall.address,
.engineOriginalTarget = h5::hooks::ScriptDispatchTarget,
.engineCommand = 3,
```

RU: Добавь обработку команды `3` в `Dispatch`. Этот callback вызывается в игровом потоке и предназначен для короткого наблюдения: не запускай из него повторный вызов SDK, собственный hook или поток. Подключение, отключение и замену callback выполняет SDK. Каталог Game API содержит и другие исследованные точки, но это не означает, что каждая поддерживает HMR. Подробности: [контракт Game API](https://github.com/Xaaalera/heroes5-game-api/blob/main/docs/mechanisms/game-bindings.md).

EN: Handle command `3` in `Dispatch` as a short game-thread observation. Do not re-enter SDK dispatch, install a direct hook or create a thread there. SDK owns callback subscription, removal and replacement. Other researched Game API sites are not automatically HMR-supported; consult the linked contract.

### Проверка SDK / Check SDK

RU: Закрой тестовую игру перед проверкой SDK. Эти команды не требуют выбранного проекта:
EN: With the test game closed, check SDK independently of the selected project:

```powershell
xkit check
xkit check --player
xkit check --console
```

RU: setup один раз спрашивает папки проектов и установленной игры с Universe; new выбирает новый проект. start запускает отдельную тестовую игру и native HMR, Ctrl+C завершает собственный сеанс. В starter plugin.cpp измени DisplayValue и сохрани: число в игре обновится. build/release создают готовый ZIP; игрок устанавливает его содержимое и запускает Heroes/Lobby как обычно. `xkit new my-mod --resources` создаёт ресурсный H5U-проект. Для C++ нужны MSVC x86/x64 Build Tools и CMake; doctor проверяет их наличие. Полные проверки сборки/владельца выполняет backend, doctor не обещает совместимость по одному найденному EXE.

EN: Configure once, create a project, then start its test game with source watch. The starter refreshes its numeric display after saves. Build/release emits a player ZIP; resource projects emit H5U. MSVC/CMake discovery is automatic. Python/tool environment management is handled by uv. Development uses this checkout; SDK wheel/PyPI distribution is not claimed.

RU: `xkit check` запускает собственную тестовую игру с двумя временными плагинами и проверяет обновление кода/ядра, сохранение состояния, откат и независимое отключение. Выбранный проект не нужен. В конце игра закрывается, команда показывает итог и путь к JSON-отчёту. Игра и редактор должны быть закрыты перед проверкой. Выпуск player-пакетов, отображение сцены и переходы карт проверяются отдельно.
EN: `xkit check` discovers the compiler and runs the canonical native HMR control without a selected project or manual Python paths. It reports progress, keeps raw diagnostics in a file and returns a nonzero exit code on failure. It is SDK runtime acceptance, not a complete player-release or graphics check.

RU: `xkit check --player` дополнительно собирает два DLL-пакета из только что проверенного кода и проверяет обычное автоматическое подключение в отдельной тестовой игре. После проверки восстанавливает загрузчик и удаляет собственные временные DLL. Итоговый отчёт связывает HMR и player-этап. Отображение сцены и переходы карт в эту проверку не входят.
EN: The player option rejects source/dependency drift between HMR and release. It runs two owned sessions sequentially, refuses a mixed SDK player installation and preserves externally changed files during restoration.

RU: `xkit check --console` отдельно проверяет семь сценариев панели: подсказки героев и существ, справку, журнал, выбор предложения и историю. Проект пользователя не устанавливается. Команда сохраняет JSON и снимок каждого сценария, закрывает свою игру и показывает полный путь к отчёту. Используются события ImGui; настоящие клавиши, мышь и зум колесиком требуют отдельной проверки. Опции `--console` и `--player` запускаются отдельно.

EN: `xkit check --console` checks seven panel scenarios: hero/creature completion, help, logs, suggestion selection and history. It does not deploy your project. It saves JSON and a capture per scenario, closes its owned game and prints the full report path. These are ImGui events; physical keyboard, mouse and wheel zoom require separate checks. Run `--console` and `--player` separately.

### Resource mod / Ресурсный мод

RU: Готовая поставка SDK содержит папку `runtime` рядом с этим README. Она нужна для консоли и подключения SDK в H5U-сеансе; C++-компилятор автору ресурсного мода не нужен. В текущем неопубликованном checkout готовые файлы собирает разработчик SDK командой `xkit sdk build`. Архив `xkit-runtime.zip` распаковывается в папку девкита, а не игры. `xkit start` проверяет соответствие файлов и исходников до запуска игры.

EN: A prepared SDK includes `runtime` beside this README. It supplies the console and SDK host for H5U sessions without requiring a C++ compiler for resource authors. In this unpublished checkout, SDK maintainers generate it with `xkit sdk build`. Extract `xkit-runtime.zip` into the devkit folder, not the game. Resource start validates files/source compatibility before game launch.

```powershell
xkit new my-resource-mod --resources
xkit build my-resource-mod
xkit release my-resource-mod
```

RU: рецепт проекта находится в `mods/my-resource-mod/mod.json`; дополнительные файлы — в его папке `files`. Build/release готовят отдельную тестовую копию при первом запуске и кладут H5U в `.local/xalkit/releases/my-resource-mod/`. Скопируй только H5U в `UserMODs` своей игры; соседний build.json — отчёт разработчика, игре он не нужен. Имя проекта можно опустить, чтобы использовать выбранный. Хеши установленного и собранного H5U совпали, метка в меню проверена в собственном SDK-сеансе. Отдельно обычный запуск только с H5U выдержал 20 секунд и штатное закрытие; снимок метки в этом отдельном процессе не снимался.
EN: Edit the recipe in `mods/my-resource-mod/mod.json`; additional files belong in its `files` folder. Build/release prepares the test copy on first use and writes H5U to `.local/xalkit/releases/my-resource-mod/`. Copy only the H5U into your game's `UserMODs`; the adjacent build.json is a developer report. Omit the project name to use the selected project. The starter appends a menu version marker without modifying the source archive. The installed/built H5U hashes and visible menu marker were verified in an SDK-owned session. A separate ordinary H5U-only startup stayed alive for 20 seconds and exited normally; its menu marker was not captured.

RU: если ProcDump установлен в локальных инструментах мастерской, SDK использует его для журнала сбоев и дампов своей тестовой игры. Перед запуском проверяет подпись Microsoft и идентичность процесса. Монитор не ищет игру по имени и не подключается к постороннему сеансу. Обычные пользователи готового DLL-мода в ProcDump не нуждаются.
EN: Optional Microsoft ProcDump integration records the explicit owned process. Raw logs and readable UTF-8 copies remain local. Tool installation and fatal-dump validation are separate tasks; a successful monitored run does not prove an intermittent startup fault is fixed.

```powershell
xkit --help
xkit language en
xkit language ru
xkit --install-completion powershell
xkit game --help
xkit diagnostics
```

RU: H5U `start` удерживает тестовую игру до её выхода или Ctrl+C. C++-компилятор для этого пути не нужен. Чтобы применить изменения ресурсов, останови сеанс, собери пакет и запусти снова. Итог сеанса доступен через `xkit diagnostics`.

EN: H5U start keeps the owned test session until game exit or Ctrl+C, without a C++ compiler. Stop, build and start again to apply resource changes. Diagnostics links the final session result.

RU: Новые проекты содержат задачи VS Code. Открой папку проекта и выбери Terminal > Run Task → xkit; Ctrl+Shift+B собирает мод. Для остановки HMR нажми Ctrl+C в терминале задачи. Задачи и координаты ошибок в Problems проверены в настоящем VS Code без C++-расширения. [Механизм задач VS Code](https://code.visualstudio.com/docs/debugtest/tasks).

EN: New projects contain VS Code tasks. Open the project folder, choose Terminal > Run Task → xkit, or Ctrl+Shift+B to build. Stop HMR with Ctrl+C in its task terminal. Actual VS Code build/Problems and HMR/source-save/terminal Ctrl+C controls passed without a C++ extension. Closing the editor or using Terminate Task is not covered by that control.

RU: После установки автодополнения открой новый PowerShell. Tab дополняет команды, параметры, проекты для `start`/`build`/`release` и карты для `start --map`. Например, `xkit release reso` + Tab предлагает `resource-check`, если такой проект создан. Для армии `xkit game army Brem ` + Tab показывает типы существ; можно начать с `CREATURE_AR`. Чтобы получить подсказки героев в PowerShell, сначала выполни `xkit game heroes`: затем `xkit game level Br` + Tab предложит `Brem`, если он есть на карте. После изменения состава героев повтори `xkit game heroes`; Tab использует последний успешный список текущего живого сеанса и сам не отправляет команд в игру. В игровой консоли список обновляется автоматически при запросе подсказки. Для переходов используй `xkit game restart`, `xkit game map NAME` и `xkit game menu`.

EN: After installing completion, open a new PowerShell. Tab completes commands, options, projects for `start`/`build`/`release` and maps for `start --map`. `xkit game army Brem ` also completes creature types; use an uppercase prefix such as `CREATURE_AR`. Run `xkit game heroes` to seed PowerShell hero suggestions; then `xkit game level Br` + Tab offers `Brem` if present. Repeat the roster query after heroes change. Tab reads the latest successful roster for the exact live session without dispatching game commands. In-game hero completion refreshes automatically. Use `xkit game restart`, `xkit game map NAME` and `xkit game menu` for map/session transitions. GNU gettext RU/EN catalogs are compiled with Babel. Game wrappers cover existing status/heroes/army/resource/teleport/level/interact APIs; they require an owned game session and appropriate adventure state. See [commands and verified scope](docs/commands.md).

RU: режим `--plugins` автоматически подключает новые плагины в работающей игре и отключает удалённые. Состояния, команды и ошибки сборки раздельны; новые исходные файлы подхватываются. Общие привязки игры вынесены в Game API. / EN: Multi-plugin watch and same-source release are verified; see [commands and limits](docs/commands.md).

[Game API](https://github.com/Xaaalera/heroes5-game-api) — shared C++ game bindings / общая библиотека привязок к игре.

RU,2026-10-04: прототип native HMR проверен полным сценарием ABI3. Сохранение C++ автоматически собирает изменения и обновляет функции, UI/events и проверенный engine CALL hook в той же игре. Состояние сохраняется, ошибочная правка откатывается; из тех же исходников собирается DLL-пакет с обычным запуском. Последний save→UI2.101s; Python51/51/native4/4 PASS. Изменения локальные, ещё не опубликованы.

EN: Verified native watch/release prototype: same-process source updates, host-owned POD state, window events/UI, one validated main-thread x86 CALL observer and same-source player DLL packaging. Arbitrary prologue hooks, schema migration, native crash recovery and external build-system integration are outside the prototype. [Start, repeat acceptance, release and limits](docs/commands.md).


## Author and related projects / Автор и связанные проекты

Автор / Author: [Xaaalera](https://github.com/Xaaalera) · [email](mailto:dampirsimpl@gmail.com) · [личный Telegram / personal Telegram](https://t.me/Victima).

- [Deployment Preview / Предиктор](https://github.com/Xaaalera/heroes5-deployment-preview).
- [Bank Reference / Справочник армий](https://github.com/Xaaalera/heroes5-bank-reference).
- [Mod Devkit / Девкит](https://github.com/Xaaalera/heroes5-mod-devkit): shared development and test tools for both DLL mods.
- [Knowledge source / Исходники базы](https://github.com/Xaaalera/heroes5-knowledge) · [public knowledge / база знаний](https://xaaalera.github.io/heroes5-knowledge/).
- [Heroes V Universe / Heroes Lobby](https://h5lobby.com/).

RU: личные проекты автора; не официальные продукты Universe. EN: Personal projects by the author, not official Universe products.

RU,2026-10-04: Python-стенд может повторно вызывать `game_control.main(argv)` в одном процессе: структурированный результат без повторного запуска интерпретатора, прежние guards проверяются каждый раз. / EN: reusable Python dispatch preserves ordinary CLI and ownership checks. [Contract](docs/commands.md).

RU, 2026-10-03: `teleport` после подтверждения положения ведёт камеру за героем текущего игрока, сохраняя zoom/rotation; флаг ответа обозначает запрос, не визуальное доказательство. Перед командой вызывающий стенд проверяет destination и владельца. / EN: verified owned-hero teleport follows the camera without changing zoom/rotation; callers must validate destination/ownership. See [commands](docs/commands.md).

## Delivery correction / Поправка к поставке — 2026-09-25

RU: отдельные EXE-загрузчики отклонены владельцем. Пользователь запускает игру через Heroes/Lobby как раньше; моды должны подключаться автоматически через DLL. Текущий прототип использует новый bin/dinput8.dll и bin/Heroes5Mods/*.dll, для справочника также нужен его H5U. Штатные бинарники Universe не заменяются. Обычный запуск до меню и автоматическое подключение двух DLL с показом проекций проверены; актуальные выпуски и ограничения указаны в репозиториях модов. Приведённые ниже команды со старым EXE — диагностика/история разработки, не инструкция игроку.

EN: separate player launcher EXEs were rejected. Players keep ordinary Heroes/Lobby startup with automatic DLL loading. The current prototype uses a new bin/dinput8.dll plus bin/Heroes5Mods/*.dll; bank reference also needs its H5U. Original Universe binaries are not replaced. Ordinary startup to the menu and both DLLs loading with visible projections were checked; current releases and limits are listed in the mod repositories. Old EXE commands below are developer diagnostics/history, not player installation.


## RU

Инструменты Windows для разработки и проверки модов **Heroes V: Повелители Орды с Universe**. Это выделенный из рабочей мастерской стенд: сборка H5U, отдельная тестовая установка, генератор полигона и команды управления собственным тестовым процессом.

Игра, профили, логи и DLL предиктора не входят в репозиторий. Нативные адреса рассчитаны только на [зафиксированную сборку](https://xaaalera.github.io/heroes5-knowledge/reference/universe-build/); это не универсальный API плагинов.

Для работы через кодинг-агента начни с [AGENTS.md](AGENTS.md): там порядок чтения, команды проверки и состав итогового отчёта.

### Быстрый старт

Этот пример создаёт ресурсный H5U-мод. Выполняй команды в Windows 10/11; нужны Git, Python 3.10+ x64 и установленная совместимая игра с Universe. Для разработки C++ плагинов используй [инструкцию native SDK](docs/commands.md): там также нужны MSVC Build Tools с компонентами x86 и CMake. Готовому моду инструменты разработки не требуются.

PowerShell, новый клон:

```powershell
git clone --recurse-submodules https://github.com/Xaaalera/heroes5-mod-devkit.git
cd heroes5-mod-devkit
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install -r requirements.txt
$env:H5_WORKSPACE = [IO.Path]::GetFullPath('../heroes5-workspace')
$env:H5_GAME_DIR = (Resolve-Path '../HeroesV-Universe').Path
New-Item -ItemType Directory -Path "$env:H5_WORKSPACE/mods" -Force | Out-Null
Copy-Item examples/menu-marker "$env:H5_WORKSPACE/mods/menu-marker" -Recurse
```

`../HeroesV-Universe` замени путём своей **существующей** установки. Рабочую папку выбирай отдельно от игры. Переменные действуют в текущем PowerShell; в новом окне задай их снова. Пример копируется один раз, затем редактируется уже в рабочей папке.

Закрой игру и редактор. Подготовка создаёт отдельные изменяемые файлы игры, а большие неизменяемые ресурсы связывает с приватным кешем. Исходная установка не используется как источник hardlink. Перед копированием SDK проверяет свободное место; правила хранения описаны выше.

```powershell
python -X utf8 scripts/mod-dev.py prepare --sandbox
python -X utf8 scripts/mod-dev.py build --sandbox --mod menu-marker
python -X utf8 scripts/mod-dev.py deploy --sandbox --mod menu-marker
python -X utf8 scripts/mod-dev.py launch --sandbox --menu
```

В меню должна появиться `[DEV: menu-marker]`. Это ожидаемый визуальный результат, который нужно проверить самому; успешный `deploy` означает установку файла, а не показ метки.

После выхода из игры:

```powershell
python -X utf8 scripts/mod-dev.py rollback --sandbox --mod menu-marker
```

Откат удаляет только файл с сохранённым собственным хешем. Если установленный H5U изменён извне, удаление блокируется.

### Мод в отдельном репозитории

Чтобы не копировать исходники мода в рабочую папку, передай `--source` сборщику:

```powershell
python scripts/mod-dev.py build --sandbox --mod army-reference --source ../heroes5-bank-reference
python scripts/mod-dev.py deploy --sandbox --mod army-reference
```

Путь указывает на папку с `mod.json`; её имя может отличаться от ID мода. Поле `id` должно совпадать с `--mod`. Выходные файлы и журнал остаются в H5_WORKSPACE. После изменения рецепта сначала выполняй build: deploy устанавливает уже собранный H5U и не перечитывает mod.json. Рецепт окон может содержать свой `object_reference` каталог; старые рецепты с `reference_windows.catalog` продолжают читаться из workspace/mods.

### Карта и команды игры

```powershell
python -X utf8 scripts/test-map.py
python -X utf8 scripts/native-probe.py launch --map WorkshopPolygon --control
python -X utf8 scripts/game_control.py status
python -X utf8 scripts/game_control.py heroes
```

`status` проверяет канал; список героев подтверждает готовность контекста карты. Сразу после запуска загрузка может ещё идти. Не повторяй вслепую команду нападения при таймауте. [Справочник команд](docs/commands.md) описывает быстрый бой, закрытие, захват и ограничения ввода.

Генератор не требует object-reference или другого мода. Он читает ресурсы своей игры и создаёт карту с 8 городами/героями, 12 хранилищами, 16 нейтральными паками и 6 аренами. Для подготовки файла вне запущенной игры: `python scripts/test-map.py --stage`. Стадированный файл не устанавливается автоматически. В публичном генераторе нет экспериментальных подписок UniverseTrigger.

### Где что хранится

| Место | Содержимое |
|---|---|
| `scripts/`, `inspect_universe.py` | Общие инструменты devkit |
| `examples/menu-marker/` | Минимальный рецепт мода для копирования |
| `H5_WORKSPACE/mods/<id>/mod.json` | Исходники и рецепт твоего мода |
| `H5_WORKSPACE/.local/test-game/` | Копия игры |
| `H5_WORKSPACE/.local/test-state/` | H5U, журналы владения, состояние канала |
| `H5_WORKSPACE/.local/staged-maps/` | Карта, подготовленная через `--stage` |
| `H5_WORKSPACE/research/` | Локальная инвентаризация и извлечённые тексты |

Без `H5_WORKSPACE` рабочая папка — клон devkit. Без `H5_GAME_DIR` исходная игра ищется в её подпапке `Heroes of Might and Magic 5 Tribes of the East`. Каталог инструментов и рабочая папка могут находиться в разных местах.

`inspect_universe.py` строит инвентаризацию и локальные копии ресурсов; они не предназначены для отправки в Git. `object_reference.py` — компилятор справочных рецептов, сами рецепты конкретных модов здесь не поставляются. Опции `--army-layout` и `--native-loader` в диагностике требуют внешнего army-reference или пакета deployment-preview соответственно; обычный `launch --control` их не требует.

### Проверка и вклад

```powershell
python -m pip install -r requirements-dev.txt
python -X utf8 -m unittest discover -s tests -v
```

51 тест проверяет архивы, владение H5U, XML, terrain, командный канал в x86-эмуляторе, watcher и размещение рабочей папки вне клона. Они не запускают игру. Живая проверка SDK выполняется отдельно через plugin-check.py; результаты описаны в [дневнике](https://xaaalera.github.io/heroes5-knowledge/reference/research-diary/).

Правила изменения и публикации SDK описаны в [CONTRIBUTING](CONTRIBUTING.md). Игра, архивы, профили и логи не коммитятся. Модули Python не загружаются из рабочей `.local/native-analysis`; зависимости устанавливаются в выбранную Python-среду. `requirements-dev.txt` добавляет Unicorn для проверок; рабочему командному каналу нужен Keystone из `requirements.txt`.

## EN

Windows tools for **Heroes V: Tribes of the East with Universe** mod development: H5U packaging, a separate test installation, a polygon generator and control of the tool's own test process. Game files, profiles, logs and the predictor DLL are not included. Native addresses support only the linked pinned build, not a universal plugin API.

For coding-agent work, start with [AGENTS.md](AGENTS.md): reading order, verification commands and reporting requirements.

### Setup and first mod

This example builds a resource-only H5U mod on Windows 10/11 using Git, Python 3.10+ x64 and a compatible Universe installation. For C++ plugin development, follow the [native SDK instructions](docs/commands.md); MSVC x86 Build Tools and CMake are also required. Players using a ready-made mod do not need development tools.

Run the shared PowerShell setup above in a new clone. Replace `../HeroesV-Universe` with your existing installation. Choose a workspace separate from the game and set both environment variables again in a new shell. Copy the example once, then edit the workspace copy.

Close the game/editor before preparation. Mutable files stay separate; large immutable assets share a private cache, never hardlinks to the original installation. SDK checks free space before copying. Follow the storage rules above. Verify the marker visually; successful deployment proves installation only. Exit the game before rollback, which preserves externally modified packages.

### Separate mod repositories

Use `--source <checkout>` with build (or cycle) to read `mod.json` directly from an external mod checkout, as in the shared commands above. Folder name may differ from the mod ID; recipe `id` must match `--mod`. Outputs/state stay in H5_WORKSPACE. Rebuild after editing a recipe: deploy installs the existing H5U and does not reread mod.json. Window recipes may embed their own `object_reference` catalog; legacy `reference_windows.catalog` references still resolve through workspace/mods.

### Maps, workspace and control

The shared map commands create WorkshopPolygon and start its terminal mailbox. `status` checks the channel; `heroes` confirms the adventure context is ready. Loading may still be in progress immediately after launch. Do not repeat a timed-out attack blindly. See the bilingual [command reference](docs/commands.md).

The generator needs no object-reference mod. It reads installed resources and creates 8 towns/heroes, 12 banks, 16 neutral packs and 6 arena objects. `--stage` writes outside the installed map and does not install it automatically. The public generator excludes experimental UniverseTrigger subscriptions.

The shared path table distinguishes tools, recipes, copied game, state and staged maps. Without `H5_WORKSPACE`, use the devkit checkout; without `H5_GAME_DIR`, look for `Heroes of Might and Magic 5 Tribes of the East` inside the workspace. Tool checkout and workspace can be separate directories.

Archive inspection produces local research files, not public Git artifacts. `object_reference.py` compiles recipes but does not ship the individual mods. `--army-layout` and `--native-loader` require external army-reference and deployment-preview artifacts respectively; ordinary `launch --control` needs neither.

### Verification and contributions

Run the shared requirements-dev/unittest commands. The 51 tests cover archives, H5U ownership, XML, terrain, emulated x86 control, watcher boundaries and external-workspace paths without launching a game. Live SDK acceptance runs separately through plugin-check.py; evidence is retained in the linked diary.

See CONTRIBUTING for SDK maintenance and publication rules. Never commit game archives, profiles or logs. Python modules are not loaded from workspace `.local/native-analysis`; install dependencies in the selected Python environment. Runtime control uses Keystone; development requirements add Unicorn for emulation.

### Нативный запуск для игроков / Native player launch

`native/player_launch.hpp` — общий Windows C++ код для загрузчиков модов: поиск соседнего `bin/H5_Game.exe` или файловый диалог, SHA-256 четырёх бинарников и проверка запущенной игры/редактора. Он не запускает игру, не ставит моды и не меняет игровые файлы сам. Python для собранного загрузчика не нужен. Правила патча и ресурсная установка принадлежат конкретному моду.

`native/player_launch.hpp` provides shared Windows C++ launcher support: discover adjacent `bin/H5_Game.exe` or show a file picker, verify four pinned binary hashes and check for running game/editor processes. It does not launch, install or patch anything by itself. Compiled consumers need no Python. Mod-specific code owns patches and resource installation.

Проверка / Check (Windows, CMake 3.21+, Visual Studio 2022 C++ x86 tools):

```sh
npm run check:native
```

Это отдельный тест границ: известный SHA-256, отсутствующий файл, неверное имя/сборка, отсутствие записи при проверке. Диалог и игра не открываются; это не проверка интерфейса или игрового запуска. / Boundary checks cover a known SHA-256, missing files, wrong executable/build and read-only validation. No dialog or game opens; this does not validate interactive UI or gameplay.

### Автоматическое подключение DLL / Automatic DLL loading

`native/mod_loader.cpp` собирается в `dinput8.dll` для `bin` поддерживаемой игры. Обычный EXE уже импортирует DirectInput8Create: библиотека передаёт вызов системной DLL по полному системному пути и проверяет сборку. На первом вызове подключаются legacy-моды из `bin/Heroes5Mods` и отдельные SDK-плагины из `bin/Heroes5Mods/Plugins/*.dll`. Legacy entry points — WorkshopDeploymentPreviewInstall и WorkshopBankReferenceInstall; SDK-плагин предоставляет Heroes5PluginInstall. Работа не выполняется в DllMain. EXE, uni.dll и um.dll остаются неизменными; графическая цепочка сохраняет исходный d3d9.dll как d3d9.universe.dll.

Ошибка инициализации завершает запуск после сообщения с кодом1114: работа с частично подключёнными модами не продолжается. Сообщение показывается после завершения InitOnce; повторный вход DirectInput во время диалога возвращает E_FAIL. Проверено на поддерживаемой игре с двумя DLL и временно отсутствующим H5U справочника (ресурс затем восстановлен). Проверка хешей игры не является проверкой подлинности DLL модов.

The bootstrap forwards DirectInput8Create to the absolute system library and validates the game build. First-call initialization loads the known legacy DLLs under bin/Heroes5Mods and discovers independent SDK plugins in bin/Heroes5Mods/Plugins/*.dll. Legacy entry points are WorkshopDeploymentPreviewInstall/WorkshopBankReferenceInstall; SDK plugins provide Heroes5PluginInstall. Initialization never runs from DllMain. Original EXE/Universe libraries remain unchanged; the graphics chain retains original d3d9.dll as d3d9.universe.dll.

A module initialization failure reports the error and exits startup with code1114 instead of continuing partly modded. Reporting occurs after InitOnce completes; reentrant input calls return E_FAIL during the dialog. The missing-bank-H5U case was tested with both DLLs installed, then the resource restored. Game hashes establish compatibility, not plugin authenticity.

`npm run check:native` выполняет две CTest-проверки: границы файловой проверки и настоящую фабрику DirectInput через переходник в неигровом процессе. / Runs two CTest checks: file-validation boundaries and the real forwarded DirectInput factory in a non-game process. No input device or game is opened by these unit checks.

Microsoft: [DirectInput8Create](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee416756(v=vs.85)) · [DllMain limits](https://learn.microsoft.com/en-us/windows/win32/dlls/dllmain) · [DLL search security](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-security).

## Standalone use / Работа вне мастерской

RU: этот репозиторий можно использовать отдельно. Начни с его README и AGENTS.md; глобальная папка мастерской не обязательна. Если есть .gitmodules, выполни `git submodule update --init --recursive` после клонирования. В связанной мастерской используй её sync-subrepos вместо создания вторых checkout.
EN: This repository can be used independently. Start with its README and AGENTS.md; the global workshop is optional. If .gitmodules exists, initialize pinned dependencies with `git submodule update --init --recursive`. In a linked workshop use its canonical dependency synchronization.

- [Devkit commands / команды SDK](https://github.com/Xaaalera/heroes5-mod-devkit/blob/main/docs/commands.md).
- [Game API contracts / контракты библиотеки](https://github.com/Xaaalera/heroes5-game-api/blob/main/docs/mechanisms/game-bindings.md).
- [Research index / карта исследований](https://xaaalera.github.io/heroes5-knowledge/reference/research-index/).

[Code standards / стандарты кода](https://github.com/Xaaalera/claude-skills).
