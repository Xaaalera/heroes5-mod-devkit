# Команды стенда / Test environment commands

## RU

Команды выполняются из клона devkit в активной Python-среде. `H5_WORKSPACE` и `H5_GAME_DIR` задаются по [README](../README.md). Управление игрой возможно только после собственного запуска с `--control`; найденный посторонний PID не становится разрешённой целью.

### Ресурсный цикл

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

Для отдельного checkout добавь `--source <папка-с-mod.json>` к build/deploy/cycle; ID рецепта должен совпадать с `--mod`. Без этого параметра используется `H5_WORKSPACE/mods/<id>`.

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

`deployment-observation` относится к наблюдателю конкретного native-loader сценария. Он не нужен для сборки H5U и не превращает готовые скрытые позиции в допустимый вход прогноза. Опции level/event и прочие параметры перечисляет `python scripts/game_control.py --help`.

### Изображение и закрытие

```powershell
powershell -NoProfile -File scripts/game-ui.ps1 -Action capture -GameProcessId <PID> -OcrTiles
powershell -NoProfile -File scripts/game-ui.ps1 -Action close -GameProcessId <PID>
```

`<PID>` замени идентификатором своего запущенного стенда. Helper сверяет путь EXE с `.local/test-game/bin/H5_Game.exe`. Capture сохраняет полный PNG; OCR распознаёт его целиком и по полосам, но может ошибаться. Обычные move/click/right-click посылают адресные сообщения, `-SystemInput` использует общую мышь и требует свободного ввода. Фоновые сообщения не доказывают работу физического ПКМ.

`background -ReturnFocusWindow <HWND>` предназначен для согласованной автономной проверки. Он меняет расположение тестового окна; запуск EXE до этого может кратко получить фокус. Для живого показа этот режим не использовать. Не выдавать пустой кадр за успешную проверку UI.

### Применимость и сохранность

Нативный запуск сверяет четыре игровых SHA-256 и исходные байты перед изменением собственного приостановленного процесса; EXE/DLL на диске не патчатся. Полная изоляция всех записей профиля вне тестовой папки не доказана. Не заменять `d3d9.dll`, `uni.dll` или `um.dll` сторонним загрузчиком ради этого стенда.

Полигон содержит проверки обычных нападений и отдельные StartCombat-арены. Они отвечают на разные вопросы. Генератор и общий devkit не дают обойти дневной максимум движения; такой обход был отдельным экспериментом предиктора.

## EN

Run commands from the devkit checkout with the Python environment active and both environment variables configured under README. Game control requires the tool's own `--control` launch; an arbitrary PID is not authorized.

### Resource workflow

The shared table defines prepare/build/deploy/status/launch/cycle/rollback. Add `--source <checkout-with-mod.json>` to build/cycle for an external recipe; its id must match --mod. Without it, use H5_WORKSPACE/mods/<id>. Deploy uses the existing package; rebuild after recipe changes. **Always pass `--sandbox` for tests**; omission targets the original installation. Existing sandboxes and foreign/externally modified packages are not overwritten or removed. Deployment checks game/editor processes, but cannot prevent a separate external launch between checking and writing. XML syntax checks do not validate every game reference.

### Terminal workflow

The shared command table gives the exact CLI. `heroes` returns Lua identifiers, unlike editor object names. `completed` is a checked result; `dispatched` is only dispatch. Adventure queries can time out during combat, and process startup does not establish map readiness. Investigate a pending command rather than repeating attacks. `finish --winner 0` is a test shortcut, not normal reward simulation. `objects` is an offline generator report.

`deployment-observation` needs its particular native-loader observer setup, not ordinary H5U development. Recorded hidden outcomes must not become prediction inputs. Consult `--help` for level/event and remaining options.

### Capture, input and shutdown

Use the shared PowerShell commands with your test-process PID. The helper checks the sandbox EXE path. Capture preserves full PNGs; OCR can misread a card. Addressed mouse messages are the default; `-SystemInput` uses the shared physical cursor and needs an exclusive input interval. Background messages do not validate physical RMB behavior.

The background action repositions the test window and can restore an explicitly supplied foreground HWND. Startup may still briefly activate the EXE; do not use this mode for an on-screen demo. Empty frames are failed capture evidence, not a successful UI check. Prefer terminal `quit`, with the normal close helper as fallback.

### Scope

Native launch validates four game hashes and original bytes before modifying its owned suspended process. On-disk EXE/DLL files remain unchanged. Complete profile-write isolation is unproven. Do not replace Universe DLLs with a generic loader. Ordinary neutral attacks and supplied StartCombat armies test different paths. The generic devkit does not bypass the daily movement cap; that was a separate predictor experiment.
