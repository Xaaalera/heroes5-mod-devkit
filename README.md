# Heroes V Mod Devkit

## Delivery correction / Поправка к поставке — 2026-09-25

RU: отдельные EXE-загрузчики отклонены владельцем. Пользователь запускает игру через Heroes/Lobby как раньше; моды должны подключаться автоматически через DLL. Текущий прототип использует новый bin/dinput8.dll и bin/Heroes5Mods/*.dll, для справочника также нужен его H5U. Штатные бинарники Universe не заменяются. Обычный запуск до меню и автоматическое подключение двух DLL с показом проекций проверены; актуальные выпуски и ограничения указаны в репозиториях модов. Приведённые ниже команды со старым EXE — диагностика/история разработки, не инструкция игроку.

EN: separate player launcher EXEs were rejected. Players keep ordinary Heroes/Lobby startup with automatic DLL loading. The current prototype uses a new bin/dinput8.dll plus bin/Heroes5Mods/*.dll; bank reference also needs its H5U. Original Universe binaries are not replaced. Ordinary startup to the menu and both DLLs loading with visible projections were checked; current releases and limits are listed in the mod repositories. Old EXE commands below are developer diagnostics/history, not player installation.


## RU

Инструменты Windows для разработки и проверки модов **Heroes V: Повелители Орды с Universe**. Это выделенный из рабочей мастерской стенд: сборка H5U, отдельная тестовая установка, генератор полигона и команды управления собственным тестовым процессом.

Игра, профили, логи и DLL предиктора не входят в репозиторий. Нативные адреса рассчитаны только на [зафиксированную сборку](https://xaaalera.github.io/heroes5-knowledge/reference/universe-build/); это не универсальный API плагинов.

Для работы через кодинг-агента начни с [AGENTS.md](AGENTS.md): там порядок чтения, команды проверки и состав итогового отчёта.

### Быстрый старт

Нужны Windows 10/11, Git, Python 3.10+ x64 и установленная игра с Universe. Для работы с исходниками самого devkit и review gate дополнительно нужен Node.js 22+. C++ Build Tools нужны конкретным нативным модам, но не сборщику H5U.

PowerShell, новый клон:

```powershell
git clone https://github.com/Xaaalera/heroes5-mod-devkit.git
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

Закрой игру и редактор. Следующая команда создаёт полноценную файловую копию: потребуется место под `bin`, `data`, `profiles`, `music`, `video`, `hwcursors`. Hardlink не используется.

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
npm ci
npm run check
```

39 тестов проверяют архивы, владение H5U, XML, terrain, командный канал в x86-эмуляторе и размещение рабочей папки вне клона. Они не запускают игру. При выделении проверены сборка полигона из пустой рабочей папки и совместимость команд мастерской; отдельный живой запуск перенесённого комплекта ещё не выполнен. Исторические проверки до переноса описаны в [дневнике](https://xaaalera.github.io/heroes5-knowledge/reference/research-diary/).

Перед push обязательно независимое ревью пяти линз и `npm run review:gate`; порядок — [CONTRIBUTING](CONTRIBUTING.md). Игра, архивы, профили и логи не коммитятся. Модули Python не загружаются из рабочей `.local/native-analysis`; зависимости устанавливаются в выбранную Python-среду. `requirements-dev.txt` добавляет Unicorn для проверок; рабочему командному каналу нужен Keystone из `requirements.txt`.

## EN

Windows tools for **Heroes V: Tribes of the East with Universe** mod development: H5U packaging, a separate test installation, a polygon generator and control of the tool's own test process. Game files, profiles, logs and the predictor DLL are not included. Native addresses support only the linked pinned build, not a universal plugin API.

For coding-agent work, start with [AGENTS.md](AGENTS.md): reading order, verification commands and reporting requirements.

### Setup and first mod

Use Windows 10/11, Git and Python 3.10+ x64. Node.js 22+ is needed for contributing/review, not normal Python tool use. C++ Build Tools belong to individual native mods, not H5U packaging.

Run the shared PowerShell setup above in a new clone. Replace `../HeroesV-Universe` with your existing installation. Choose a workspace separate from the game and set both environment variables again in a new shell. Copy the example once, then edit the workspace copy.

Close the game/editor before `prepare --sandbox`. It makes separate file copies of the listed game directories, not hardlinks, so reserve enough disk space. Build/deploy/launch the marker using the shared commands. Verify `[DEV: menu-marker]` visually; successful deployment only proves the file was installed. Exit the game before rollback. Rollback refuses to delete a package modified outside the tool.

### Separate mod repositories

Use `--source <checkout>` with build (or cycle) to read `mod.json` directly from an external mod checkout, as in the shared commands above. Folder name may differ from the mod ID; recipe `id` must match `--mod`. Outputs/state stay in H5_WORKSPACE. Rebuild after editing a recipe: deploy installs the existing H5U and does not reread mod.json. Window recipes may embed their own `object_reference` catalog; legacy `reference_windows.catalog` references still resolve through workspace/mods.

### Maps, workspace and control

The shared map commands create WorkshopPolygon and start its terminal mailbox. `status` checks the channel; `heroes` confirms the adventure context is ready. Loading may still be in progress immediately after launch. Do not repeat a timed-out attack blindly. See the bilingual [command reference](docs/commands.md).

The generator needs no object-reference mod. It reads installed resources and creates 8 towns/heroes, 12 banks, 16 neutral packs and 6 arena objects. `--stage` writes outside the installed map and does not install it automatically. The public generator excludes experimental UniverseTrigger subscriptions.

The shared path table distinguishes tools, recipes, copied game, state and staged maps. Without `H5_WORKSPACE`, use the devkit checkout; without `H5_GAME_DIR`, look for `Heroes of Might and Magic 5 Tribes of the East` inside the workspace. Tool checkout and workspace can be separate directories.

Archive inspection produces local research files, not public Git artifacts. `object_reference.py` compiles recipes but does not ship the individual mods. `--army-layout` and `--native-loader` require external army-reference and deployment-preview artifacts respectively; ordinary `launch --control` needs neither.

### Verification and contributions

Run the shared requirements-dev/unittest/npm commands. The 39 tests cover archives, H5U ownership, XML, terrain, emulated x86 control and external-workspace paths without launching a game. Extraction checks also built a polygon in an empty workspace and exercised existing workshop commands. No separate live-game acceptance of this extracted distribution is claimed. Historical checks remain in the linked diary.

Independent five-lens review and `npm run review:gate` are mandatory before push; see CONTRIBUTING. Never commit game archives, profiles or logs. Python modules are not loaded from workspace `.local/native-analysis`; install dependencies in the selected Python environment. Runtime control uses Keystone; development requirements add Unicorn for emulation.

### Нативный запуск для игроков / Native player launch

`native/player_launch.hpp` — общий Windows C++ код для загрузчиков модов: поиск соседнего `bin/H5_Game.exe` или файловый диалог, SHA-256 четырёх бинарников и проверка запущенной игры/редактора. Он не запускает игру, не ставит моды и не меняет игровые файлы сам. Python для собранного загрузчика не нужен. Правила патча и ресурсная установка принадлежат конкретному моду.

`native/player_launch.hpp` provides shared Windows C++ launcher support: discover adjacent `bin/H5_Game.exe` or show a file picker, verify four pinned binary hashes and check for running game/editor processes. It does not launch, install or patch anything by itself. Compiled consumers need no Python. Mod-specific code owns patches and resource installation.

Проверка / Check (Windows, CMake 3.21+, Visual Studio 2022 C++ x86 tools):

```sh
npm run check:native
```

Это отдельный тест границ: известный SHA-256, отсутствующий файл, неверное имя/сборка, отсутствие записи при проверке. Диалог и игра не открываются; это не проверка интерфейса или игрового запуска. / Boundary checks cover a known SHA-256, missing files, wrong executable/build and read-only validation. No dialog or game opens; this does not validate interactive UI or gameplay.

### Автоматическое подключение DLL / Automatic DLL loading

`native/mod_loader.cpp` собирается в `dinput8.dll` для `bin` поддерживаемой игры. Обычный EXE уже импортирует DirectInput8Create: библиотека передаёт вызов системной DLL по полному системному пути, а на первом вызове проверяет сборку и подключает известные DLL из `bin/Heroes5Mods`. Работа не выполняется в DllMain. Модам доступны точки WorkshopDeploymentPreviewInstall и WorkshopBankReferenceInstall; отсутствие необязательной DLL допустимо. Не заменяются оригинальные EXE, d3d9.dll, uni.dll и um.dll.

Ошибка инициализации завершает запуск после сообщения с кодом1114: работа с частично подключёнными модами не продолжается. Сообщение показывается после завершения InitOnce; повторный вход DirectInput во время диалога возвращает E_FAIL. Проверено на поддерживаемой игре с двумя DLL и временно отсутствующим H5U справочника (ресурс затем восстановлен). Проверка хешей игры не является проверкой подлинности DLL модов.

The source builds a bin/dinput8.dll bootstrap for the supported game. The normal EXE already imports DirectInput8Create. Forwarding uses the absolute Windows system library; first-call initialization validates the build and loads the two known DLLs under bin/Heroes5Mods, never from DllMain. Entry points are WorkshopDeploymentPreviewInstall and WorkshopBankReferenceInstall; an absent optional DLL is allowed. Original game EXE/DLL files are not replaced.

A module initialization failure reports the error and exits startup with code1114 instead of continuing partly modded. Reporting occurs after InitOnce completes; reentrant input calls return E_FAIL during the dialog. The missing-bank-H5U case was tested with both DLLs installed, then the resource restored. Game hashes establish compatibility, not plugin authenticity.

`npm run check:native` выполняет две CTest-проверки: границы файловой проверки и настоящую фабрику DirectInput через переходник в неигровом процессе. / Runs two CTest checks: file-validation boundaries and the real forwarded DirectInput factory in a non-game process. No input device or game is opened by these unit checks.

Microsoft: [DirectInput8Create](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee416756(v=vs.85)) · [DllMain limits](https://learn.microsoft.com/en-us/windows/win32/dlls/dllmain) · [DLL search security](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-security).
