# Команды стенда / Test environment commands

## 2026-10-04 — levelup-state / нативное окно повышения уровня

RU: `game_control.py levelup-state` пассивно читает жизненный цикл CLevelUpBox закреплённой сборки. Observer ставится до первого resume собственного процесса: ctor0x6a901a/dtor0x6aa430, original bytes проверены. Отдельные RX/RW страницы не пересекают command/result buffers. Поля available/stable/open/objects/created/destroyed/sequence; предел4 объекта. PID creation, patch bytes, magic, even sequence, баланс объектов и primary vtable проверяются. Ошибка/неизвестность не означает закрытое окно.

EN: This tracks the specific level-up object's lifetime, not general screen readiness. Signature changes, overflow/unknown destruction/corrupt counts fail closed. Installer restores original code on failed patching. Native emulation verifies register/flag/original-instruction preservation and nested lifetimes; decoder/failure rollback tests pass. Live forced1→2 and production cleanup both observe creation and destruction without OCR or focus/cursor changes.

RU: console VM не предоставляет type/pcall; нативное чтение окна не использует Lua. Обычные игровые DLL и данные прогноза не изменены. Вызовы skill_1/MB_Button_Ok остаются прежними и отправляются только при подтверждённом CLevelUpBox. / Other dialogs and adventure readiness need separate checks.

## 2026-10-03 — console Lua builtins / функции console VM

RU: в проверенной Universe console VM type и pcall отсутствуют. Запрос `(workshop_arena_1 and 1 or 0)..','..(StartCombat and 1 or 0)..','..(type and 1 or 0)..','..(pcall and 1 or 0)` вернул1,1,0,0. Запрос с type завершился тайм-аутом без result; это не доказательство отсутствия функции арены. Не переносить предположения обычного Lua в этот контекст.

EN: A SetGameVar RPC marker after workshop_arena_1 verified that the call returned; a separate native lifecycle snapshot confirmed placement generation0→1. Neither establishes rendering or prediction accuracy. Read-only global probes do not install builtins or change game scripts.

## 2026-10-03 — blank capture rejection / пустые кадры

RU: game-ui.ps1 сохраняет PNG, затем проверяет цвета сетки32×32. Если найдено не более двух цветов, capture завершается ошибкой до OCR. Это отсекает воспроизведённую пустую чёрно-белую поверхность PrintWindow; успешный PrintWindow сам по себе не доказывает снимок игры. Прохождение фильтра не подтверждает фазу UI или актуальность кадра.

EN: PNG stays at the path stated in the error. A sampled frame with at most two colours is rejected before OCR; passing this heuristic does not establish phase, freshness or correct rendering. No focus/cursor change or automatic retry is added. Tests reject black/white/two-colour fixtures and accept a richer fixture; retained blank rejected, retained adventure frame accepted. All9 game-control tests pass with no skips.

## 2026-10-03 — runtime-state

RU: `game_control.py runtime-state` возвращает stable/event_commands_pending и context1(adventure)/21(combat_console) script_commands_pending/registered_listener_count. Проверяет собственный PID/creation и границы native containers; Lua не выполняется. Listeners не означают активные VM или текущий экран; empty queues не доказывают adventure readiness.

EN: This passive diagnostic reports native queues/listeners without dispatching Lua. Consumers must separately confirm their expected scene or hero response. SDK8 tests cover decoder bounds/cycles and queue flags. The workshop uses queue drain before its existing hero confirmation.

## 2026-10-03 — full-combat test map / тестовая карта с полным боем

RU: test-map.py задаёт AllowQuickCombat=false authored heroes/monsters, чтобы даже сильный герой проходил настоящую расстановку. Не меняет сохранённый профиль игрока; лог содержитquick_combat_allowed=false. Армии/количества/позиции сохранены, hash карты меняется.

EN: Authored fixtures require full combat; automatic quick-combat results are not placement evidence. A fresh generated split/grade control matched3/3 units. The workshop's broad round still has a post-results readiness failure; full campaign completion is not established.

## 2026-10-03 — teleport camera / камера после телепорта

RU: после readback координат и существующей2s settling проверки `teleport` вызывает MoveCamera для героя текущего игрока, сохраняя zoom/rotation. Ответ `camera_follow_requested` означает отправленную команду; изображение проверяется отдельно. Вызывающий стенд должен выбирать проходимую клетку до телепорта и правильного владельца героя.

EN: An owned hero remains visible at the new location through camera follow after verified transfer; other owners do not move the camera. SDK7 tests pass; one workshop live capture shows hero/selected portrait and normal closure. Preserve the distinction between command completion and visual confirmation.

## Delivery correction / Поправка к поставке — 2026-09-25

RU: отдельные EXE-загрузчики отклонены владельцем. Пользователь запускает игру через Heroes/Lobby как раньше; моды должны подключаться автоматически через DLL. Текущий прототип использует новый bin/dinput8.dll и bin/Heroes5Mods/*.dll, для справочника также нужен его H5U. Штатные бинарники Universe не заменяются. Обычный запуск до меню и автоматическое подключение двух DLL с показом проекций проверены; актуальные выпуски и ограничения указаны в репозиториях модов. Приведённые ниже команды со старым EXE — диагностика/история разработки, не инструкция игроку.

EN: separate player launcher EXEs were rejected. Players keep ordinary Heroes/Lobby startup with automatic DLL loading. The current prototype uses a new bin/dinput8.dll plus bin/Heroes5Mods/*.dll; bank reference also needs its H5U. Original Universe binaries are not replaced. Ordinary startup to the menu and both DLLs loading with visible projections were checked; current releases and limits are listed in the mod repositories. Old EXE commands below are developer diagnostics/history, not player installation.


## RU

Команды выполняются из клона devkit в активной Python-среде. `H5_WORKSPACE` и `H5_GAME_DIR` задаются по [README](../README.md). Управление игрой возможно только после собственного запуска с `--control`; найденный посторонний PID не становится разрешённой целью.

### Ресурсный цикл

Полигон после дополнения 2 октября2026 содержит36 нейтральных составов: прежние `pack_0..15` и20 проверок отдельных правил `pack_16..35`. `python scripts/test-map.py --stage` собирает проверенную копию вне игры; без `--stage` заменяется только собственная карта при закрытой игре. Дополнительные случаи проверяют количества одиночного пака, размеры, грейды, прикрытие стрелков, гоблинов и связанных существ, порядок входов, семь крупных стеков и повтор типа. Это тестовые данные, не установленное100% покрытие алгоритма.

`python scripts/mod-dev.py <команда> --sandbox --mod <id>`:

| Команда | Результат |
|---|---|
| prepare | Создаёт копию игры; существующую копию не перезаписывает |
| build | Собирает H5U из `mods/<id>/mod.json`, сохраняет хеши входов |
| deploy | Проверяет хеши и устанавливает только собственный пакет |
| status | Показывает пути и состояние установки |
| launch --menu | Запускает меню тестовой копии |
| cycle | Build + deploy; запуск только с дополнительным `--launch` |
| rollback | Удаляет собственную неизменённую установку |

Для отдельного checkout добавь `--source <папка-с-mod.json>` к build/cycle; ID рецепта должен совпадать с `--mod`. Без этого параметра используется `H5_WORKSPACE/mods/<id>`. Deploy устанавливает ранее собранный пакет; при правке рецепта сначала нужен build.

**Всегда указывай `--sandbox` для тестов.** Без него mod-dev работает с исходной установкой. Deploy/rollback блокируются при работающей игре или редакторе; внешний запуск между проверкой и записью полностью не исключён. XML-парсинг не доказывает корректности всех игровых ссылок.

### Командный канал

Сначала `python scripts/native-probe.py launch --map WorkshopPolygon --control`, затем `python scripts/game_control.py <команда>`:

| Команда | Назначение / проверка |
|---|---|
| status | PID, поколение процесса, сигнатура, heartbeat |
| heroes | Реальные Lua-имена героев; на полигоне главный — Brem |
| hero Brem | Координаты, уровень, движение |
| teleport Brem 25 50 | Перемещение с проверкой конечной клетки |
| move Brem 26 50 | Обычный ход с ожиданием координат |
| interact Brem pack_8 | Обычное нападение; результат dispatch ещё не означает начало боя |
| confirm | Подтверждение расстановки |
| finish --winner 0 | Тестовое завершение в пользу атакующего; не имитация обычных наград |
| results | Переход с экрана результатов; возврат проверяется через hero |
| quit | Штатное закрытие с ожиданием выхода процесса |
| serve | JSONL-сессия для нескольких команд без повторной загрузки |
| eval "GetHeroLevel('Brem')" | Числовое/строковое выражение Lua приключений |
| console "@SetPlayerResource(1,6,100000)" | Команда консоли; эффект нужно проверить отдельно |
| resource 1 6 | Прочитать золото игрока 1; `--amount` задаёт итог |
| creature Brem CREATURE_PEASANT | Прочитать число; `--count` задаёт итог, не прибавку |
| day | Текущий игровой день |
| objects | Офлайн-каталог генератора, не живой список объектов |

`completed` означает полученный/проверенный результат; `dispatched` — лишь отправку. Контекст приключений может не отвечать в бою. Успех процесса launch не равен готовности карты. Не отправляй действие повторно, пока не выяснено состояние предыдущего запроса.

Для обычного запуска с DLL-модами используй `native-probe.py launch --map WorkshopPolygon --control --observe-deployment`. `deployment-observation` относится к включённому так наблюдателю; прежний диагностический `--native-loader` также включает его. Он не нужен для сборки H5U и не превращает готовые скрытые позиции в допустимый вход прогноза. Опции level/event и прочие параметры перечисляет `python scripts/game_control.py --help`.

### Изображение и закрытие

```powershell
powershell -NoProfile -File scripts/game-ui.ps1 -Action capture -GameProcessId <PID> -OcrTiles
powershell -NoProfile -File scripts/game-ui.ps1 -Action close -GameProcessId <PID>
```

`<PID>` замени идентификатором своего запущенного стенда. Helper сверяет путь EXE с `.local/test-game/bin/H5_Game.exe`. Capture сохраняет полный PNG; OCR распознаёт его целиком и по полосам, но может ошибаться. Обычные move/click/right-click посылают адресные сообщения, `-SystemInput` использует общую мышь и требует свободного ввода. Фоновые сообщения не доказывают работу физического ПКМ.

Для фонового теста `native-probe.py launch --map <карта> --control --background` запрашивает `STARTF_USESHOWWINDOW/SW_SHOWNA`. Хелпер `game-ui.ps1 background` требует PID, сохранённый до запуска HWND и исходный прямоугольник `ClipCursor` (`-RestoreClip -ReturnClipLeft/Top/Right/Bottom`); он ставит тестовому окну `WS_EX_NOACTIVATE`, переносит его за рабочий стол и проверяет восстановление фокуса/курсора. Результат `background_window` обязателен перед боем. **Первый контроль 25 сентября был отклонён:** игра повторно захватила фокус/курсор, песочница закрылась без боя. Поправка с `WS_EX_NOACTIVATE` и восстановлением `ClipCursor` пока не проверена живым запуском; не запускать так во время работы владельца за ПК. Стенд отказывает при любом уже работающем `H5_Game.exe`; проверку не обходить. Пустой кадр не является успешным снимком UI.

### Применимость и сохранность

Поправка 2 октября 2026: стенд сверяет и переставляет два import descriptors закреплённого EXE только в памяти собственного приостановленного процесса: Granny перед Universe d3d9. Защита страницы восстанавливается. Один контроль без отладчика загрузил карту: Granny `0x50000000`, Universe d3d9 `0x7afb0000`, штатный выход. Игровые файлы на диске сохранены; пользовательская поставка этим не исправляется.

Нативный запуск сверяет четыре игровых SHA-256 и исходные байты перед изменением собственного приостановленного процесса; EXE/DLL на диске не патчатся. Полная изоляция всех записей профиля вне тестовой папки не доказана. Не заменять `d3d9.dll`, `uni.dll` или `um.dll` сторонним загрузчиком ради этого стенда.

Полигон содержит проверки обычных нападений и отдельные StartCombat-арены. Они отвечают на разные вопросы. Генератор и общий devkit не дают обойти дневной максимум движения; такой обход был отдельным экспериментом предиктора.

## EN

October 2, 2026: the probe verifies and swaps two pinned EXE import descriptors only in its own suspended process, loading Granny before Universe d3d9 and restoring page protection. A debugger-free control loaded the map with Granny at `0x50000000` and Universe d3d9 at `0x7afb0000`, then exited normally. On-disk game files are preserved; this does not repair player delivery.

The expanded polygon has36 neutral compositions: original `pack_0..15` plus20 targeted controls `pack_16..35`. `python scripts/test-map.py --stage` builds a validated staged copy; omitting it replaces only the owned map while the game is closed. New cases cover singleton quantities, footprints, grades, shooter support, Goblin partners, input order, seven large stacks and duplicate types. These fixtures do not establish complete algorithm coverage.

Run commands from the devkit checkout with the Python environment active and both environment variables configured under README. Game control requires the tool's own `--control` launch; an arbitrary PID is not authorized.

### Resource workflow

The shared table defines prepare/build/deploy/status/launch/cycle/rollback. Add `--source <checkout-with-mod.json>` to build/cycle for an external recipe; its id must match --mod. Without it, use H5_WORKSPACE/mods/<id>. Deploy uses the existing package; rebuild after recipe changes. **Always pass `--sandbox` for tests**; omission targets the original installation. Existing sandboxes and foreign/externally modified packages are not overwritten or removed. Deployment checks game/editor processes, but cannot prevent a separate external launch between checking and writing. XML syntax checks do not validate every game reference.

### Terminal workflow

The shared command table gives the exact CLI. `heroes` returns Lua identifiers, unlike editor object names. `completed` is a checked result; `dispatched` is only dispatch. Adventure queries can time out during combat, and process startup does not establish map readiness. Investigate a pending command rather than repeating attacks. `finish --winner 0` is a test shortcut, not normal reward simulation. `objects` is an offline generator report.

For normal startup with DLL mods use `native-probe.py launch --map WorkshopPolygon --control --observe-deployment`. `deployment-observation` requires that observer setup; the legacy diagnostic --native-loader also enables it. It is not ordinary H5U development. Recorded hidden outcomes must not become prediction inputs. Consult `--help` for level/event and remaining options.

### Capture, input and shutdown

Use the shared PowerShell commands with your test-process PID. The helper checks the sandbox EXE path. Capture preserves full PNGs; OCR can misread a card. Addressed mouse messages are the default; `-SystemInput` uses the shared physical cursor and needs an exclusive input interval. Background messages do not validate physical RMB behavior.

For a background test, `native-probe.py launch --map <map> --control --background` requests `STARTF_USESHOWWINDOW/SW_SHOWNA`. `game-ui.ps1 background` requires the PID, pre-launch foreground HWND and original `ClipCursor` rectangle (`-RestoreClip -ReturnClipLeft/Top/Right/Bottom`); it applies `WS_EX_NOACTIVATE`, moves the sandbox window off-screen and checks focus/cursor restoration. Require `background_window` before battle commands. **The first September25 control was refused:** the game reclaimed foreground/cursor clipping, and the owned sandbox closed without combat. The `WS_EX_NOACTIVATE`/`ClipCursor` correction is still unverified in a live launch; do not use it while the owner works at the PC. The probe refuses any second `H5_Game.exe`; never bypass that guard. Empty frames are failed captures. Prefer terminal `quit` for shutdown.

### Scope

Native launch validates four game hashes and original bytes before modifying its owned suspended process. On-disk EXE/DLL files remain unchanged. Complete profile-write isolation is unproven. Do not replace Universe DLLs with a generic loader. Ordinary neutral attacks and supplied StartCombat armies test different paths. The generic devkit does not bypass the daily movement cap; that was a separate predictor experiment.
## 2026-10-04 — reusable Python dispatcher / повторное использование диспетчера

RU: `game_control.main(argv)` принимает список CLI-аргументов и возвращает тот же структурированный результат без stdout. Импорт native-probe переиспользуется в этом процессе; каждый вызов по-прежнему проверяет текущий PID/creation time/patches. Не хранить открытый game handle между загрузками. `main()` без argv сохраняет обычный CLI JSON stdout и существующий serve. EN: Caller must set its workspace before importing the SDK. Reuse changes client startup overhead, not game readiness; all ownership/signature/readback guards remain. Full SDK46/46 and owned three-battle consumer control PASS; ordinary game DLL behavior unchanged.
