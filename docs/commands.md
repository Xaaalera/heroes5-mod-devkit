# Команды стенда / Test environment commands

## Local keyboard isolation update / Локальное исправление захвата клавиатуры

RU: новая версия в исходниках забирает игровые нажатия, когда видимая консоль имеет фокус ввода. Например, I вводится в консоли и не открывает снаряжение героя позади неё; после закрытия консоли I снова работает в игре. Обработчик выполняется до игровых горячих клавиш, а служебные и неизвестные события проходят как раньше. Отдельная настройка пользователя не нужна.

EN: The local source update captures gameplay key presses while the visible console owns keyboard focus. I no longer opens equipment behind it; after hiding the console, I works in the game. Capture runs before gameplay bindings and preserves unknown/system events. No extra user setting is required.

RU: проверка в одном процессе охватывает новое окно консоли и изменённое ядро версии2, затем нажатия внутри и после закрытия. Проверен один переход с удерживаемой PageDown: после отпускания клавиша не осталась нажатой, а следующий нажим на карте менял масштаб. В отдельной парной проверке щелчки по портрету под консолью не меняли героя, а после закрытия открывали окно второго героя. Эти результаты ограничены проверенной картой и действиями; все экраны, сочетания клавиш и виды перетаскивания пока не проверены. Исправление ещё не выпущено в готовом архиве.

EN: One owned process verifies console HWND replacement and edited core version2 before paired key checks. One held-PageDown hide/release transition confirms no stuck key and a working later map zoom. A separate portrait-click pair leaves the underlying hero unchanged while covered, then opens the second hero after hiding. This covers the tested map/actions; every screen, shortcut and drag gesture remains unverified. The fix is not yet in a published ready archive.

RU, 2026-10-07: текущая графическая цепочка проверена в полном ресурсном сеансе SDK и нативном HMR. Подтверждены две независимые DLL, замена ядра и функций, состояние, отключение callbacks и откат. Два готовых пакета прошли обычный запуск игры. В консоли проверены физические клавиши, изменение размера мышью и прокрутка логов без движения камеры. Редактор и другие версии Windows требуют отдельных проверок; текущая сборка ещё не опубликована.
EN: The current graphics chain passes resource SDK lifecycle and native HMR: two independent DLLs, core/function replacement, state transfer, callback teardown and rollback. Player packages pass ordinary startup. Console checks cover physical keyboard input, mouse resizing and log scrolling without camera movement. Editor and other Windows versions require separate checks; current changes remain unpublished.

## Хранение тестовых установок / Test storage

- `xkit storage status` — число подготовленных установок и путь общего кэша.
- `xkit storage clean --keep 3` — объединить одинаковые большие ресурсы, сохранить снимки старых копий и оставить три установки. Допустимо оставить от нуля до трёх.
- `xkit storage restore ARCHIVE` — восстановить закрытую установку из `retired-game.zip` и её общих ресурсов. При занятом лимите сначала освободи место командой `clean`.

RU: SDK использует стандартные hard links для больших неизменяемых архивов и мультимедиа. Ссылки ведут на отдельный приватный кэш, не на установленную игру. Кэш защищён от записи. Изменяемые файлы имеют свои копии. Это экономит фактическое место; сумма размеров путей в Проводнике может учитывать один общий файл несколько раз.

RU: Очистка требует закрытой игры и редактора, проверяет маркер SDK, хеш EXE и границы путей. Неизвестные папки не удаляются. Снимок содержит изменяемые файлы и проверенный манифест общих ресурсов. При ошибке проверки снимка исходная тестовая копия сохраняется. Общая блокировка Portalocker не позволяет двум операциям хранения пересекаться. Заблокированная старая копия может помешать созданию новой; SDK откажет вместо создания четвёртой.

EN: Sharing uses NTFS hardlinks into a private read-only cache, never into the original installation. Mutable files stay independent. Explorer logical size can count linked paths repeatedly. Cleanup verifies SDK ownership, executable identity and confined paths with game/editor closed; unknown folders remain untouched. CRC-checked snapshots retain mutable files and digest/size references to the persistent cache. Portalocker serializes preparation/cleanup/restoration; a busy retirement candidate cannot permit a fourth installation. Free-space preflight runs before copying.

Sources: [NTFS links](https://learn.microsoft.com/en-us/windows/win32/fileio/hard-links-and-junctions), [Portalocker](https://portalocker.readthedocs.io/en/latest/), [Python filesystem APIs](https://docs.python.org/3/library/shutil.html).

RU: Очистка также оставляет два последних дампа сбоя распакованными. Остальные сжимаются в GZIP без потери содержимого; перед удалением большой распакованной копии проверяется контрольная сумма. Для внешнего отладчика используй `xkit storage restore-dump FILE.dmp.gz`. Отчёты и сведения о принадлежности дампа сохраняются; встроенное чтение идентификатора понимает архивный вариант.

EN: Cleanup keeps the two newest raw crash dumps and gzip-compresses older dumps without losing bytes. SHA verification precedes removal of the raw duplicate. Restore an archived dump for an external debugger with `xkit storage restore-dump FILE.dmp.gz`. Reports and provenance remain; the built-in identity reader supports compressed dumps.

RU: Команды `xkit game` по умолчанию показывают понятный результат: имена героев, уровень, количество существ или ресурсов. Для скрипта добавь `--json`, например `xkit game heroes --json`. JSON выводится в stdout, журнал операции — в stderr; их можно перенаправлять отдельно. Игровая консоль использует тот же формат понятных ответов.

EN: `xkit game` commands show readable results by default. Add `--json` for scripts, for example `xkit game heroes --json`. JSON goes to stdout and operation logs to stderr, so they can be redirected separately. The in-game console shares the readable formatter.

RU: Прямой запуск диагностических `game_control.py`, `native-probe.py` и `mod-dev.py` тоже сохраняет итог и длительность в `.local/xalkit/logs/events.jsonl` рабочей папки. Служебные сообщения идут в stderr; прежний ответ stdout сохраняется. У отдельного запуска есть собственные идентификаторы сеанса и операции. Это инструменты диагностики; обычная работа начинается с `xkit`.

EN: Direct diagnostic script entry points also record outcome and duration in the workspace's `.local/xalkit/logs/events.jsonl`. Diagnostics go to stderr and existing stdout results are preserved. Each standalone invocation has its own session and operation IDs. Use `xkit` for the ordinary workflow.

RU: Для дочерних инструментов, запущенных SDK, журнал сохраняет общий `session_id` и ссылку `parent_operation_id` на исходную команду. У дочерней операции остаётся собственный `operation_id`: по ним можно связать этапы одного запуска в IDE, не смешивая несколько одновременно выполняющихся команд. Идентификаторы диагностики не дают разрешения управлять игрой.

EN: SDK-launched child tools inherit the log session and parent operation link while retaining their own operation IDs. This connects one command's stages across processes without merging concurrent operations. Log identifiers do not authorize game control.

RU: Итог замены ядра содержит общую `duration_seconds`: от начала проверки изменённых исходников до подтверждённого результата всех плагинов либо окончания подтверждённого отката. Сборка имеет отдельное время. `timing_scope` описывает границы замера; время до обнаружения сохранённого файла и последующая перерисовка интерфейса в этот интервал не входят.

EN: Core replacement records total `duration_seconds` from its source scan through verified observations from all participants, or confirmed rollback. Build time remains separate. `timing_scope` names the measurement boundary; file-save detection delay and subsequent UI redraw are outside this interval.

## Консоль в игре / In-game console

### Проверка панели / Panel check

Запусти `xkit check --console`, когда игра и редактор закрыты. SDK откроет свою тестовую карту, выполнит семь сценариев панели, сохранит JSON и снимки, затем закроет игру. Проект пользователя не устанавливается. В результате видны время и полный путь к отчёту; при ошибке общий результат остаётся неуспешным, даже если сами сценарии прошли, но очистка не удалась.

EN: Run `xkit check --console` with the game and editor closed. It opens an owned test map, checks seven panel scenarios, saves JSON/captures and closes the game without deploying your project. It prints total time and the report path. Failed cleanup prevents a successful overall result.

RU/EN: Этот контроль использует события ImGui и не подтверждает физические клавиши, мышь или колесико камеры. / This control uses ImGui events; it does not certify physical keyboard, mouse or camera-wheel input. `xkit check` still checks native HMR; `xkit check --player` also checks released DLL packages.

RU: Технический журнал контроллера отмечает начало и подтверждённое завершение явного вызова плагина в игровом потоке. Записи содержат имя операции, процесс, результат и длительность. Если записи завершения нет, результат ещё не подтверждён; это само по себе не определяет причину сбоя. Эти сведения помогают разбирать первый запуск и не меняют ответы команд.

EN: The controller's technical log records the start and confirmed return of an explicit plugin call on the game thread, with operation name, process, result and duration. A missing completion record means the result is unconfirmed; it does not identify a crash cause. Command responses are unchanged.

RU: Кнопка «Логи» открывает панель внутри игры. Во вкладке «Команды» ввод находится снизу, ответы — над ним. Тяни край панели, чтобы изменить ширину или высоту.

- `Ctrl+Enter` выполняет введённую команду.
- `Tab` дополняет команду или текущий аргумент. Например, `army ` предлагает героев, а `army Brem ` — типы существ.
- В списке подсказок стрелки выбирают предложение, `Enter` вставляет его, `Esc` закрывает список. Пока он открыт, клавиши не попадают в редактор команды.
- Опции можно вводить перед аргументами: `army --count 100 Br` + `Tab` дополняет имя героя. `help ar` + `Tab` предлагает `help army`.
- Стрелки вверх/вниз в поле ввода выбирают предыдущие команды. Кнопка «История» также позволяет выбрать команду.
- `help` показывает команды и их аргументы; `help army` или `army --help` объясняет работу с армией.
- `army Brem CREATURE_ARCHER` читает количество лучников. `--count 100` задаёт итоговое количество, а не добавляет ещё 100.
- `trace` читает сообщения модулей. Он не выполняет каждую строку журнала как команду. `trace --level warning --limit 10` показывает до десяти предупреждений и ошибок.
- «Вернуться на карту» скрывает панель. «Логи» открывает её снова.

Поиск во вкладке журнала проверяет запись вместе с ответом команды. Совпадение в ответе сохраняет и заголовок; если совпадений нет, скрывается вся запись.

EN: Logs opens the in-game panel. Commands has its input at the bottom and output above it. Resize the panel by dragging its edges. Ctrl+Enter runs a command; Tab completes command names and arguments; Up/Down recalls history. `help`, `help army` and `army --help` explain commands. `army Brem CREATURE_ARCHER --count 100` sets the total count. Plain `trace` reads module messages and does not execute log lines. Return to game hides the panel.

EN: Options may precede arguments: Tab completes the hero in `army --count 100 Br` and the command in `help ar`. Log search treats the headline and command result as one entry: matching result text retains its headline; no match hides both.

EN: In the completion list, arrows choose a suggestion, Enter inserts it and Esc closes the list. The command editor does not receive keystrokes while this popup is open.

### Снимок игры / Game screenshot

В терминале выполни `xkit game screenshot`; в игровой консоли — `screenshot`. Команда сохраняет PNG в `.local/xalkit/captures` папки проектов и показывает полный путь. Для скрипта используй `xkit game screenshot --json`.

RU: Снимается текущий игровой кадр: это может быть карта, меню или ещё заставка. Команда ждёт новый завершённый снимок, сохраняет исходный TGA игры и создаёт PNG через [Pillow](https://pillow.readthedocs.io/en/stable/reference/Image.html). Несколько кадров от одного запроса не вызывают повтор команды. PNG содержит игровой рендер; сведения модулей и журнал SDK доступны через `xkit diagnostics`.

EN: Run `xkit game screenshot` in the terminal or `screenshot` in the in-game console. The command saves a PNG under the workspace's `.local/xalkit/captures` and prints its path. Add `--json` for scripts. It captures the current game frame, which may be a map, menu or intro. A new complete frame is required; original game TGAs are retained. Pillow converts the selected complete frame to PNG. Multiple captures from one request do not trigger replay. SDK module/log diagnostics remain available through `xkit diagnostics`.

RU: В подключённом сеансе захват отправляется через активное ядро SDK как игровое событие. Завершение диагностического клиента не отключает плагины. Если ядро отказало или не поддерживает захват, запрос не повторяется другим способом; подробности отказа остаются в журнале. После обновления исходников самого SDK сначала выполни `xkit sdk build`.

EN: In a connected session, capture uses the active SDK core's named game event. Exiting the diagnostic client does not stop plugins. A refusal or unsupported capture is not replayed through another route; diagnostic details remain in the log. After updating SDK source, run `xkit sdk build` first.

RU: При сбое терминал и игровая консоль показывают одинаковое сообщение и путь к журналу. Проверь запущенный тестовый сеанс и доступ к папке диагностики; техническая причина сохраняется в журнале, а не подменяет подсказку.

EN: Terminal and in-game console preserve the same registered error and diagnostic path. Check the active test session and write access to the diagnostics folder; technical details stay in the journal.

RU: Если новая DLL не прошла проверку, а её отключение или откат тоже не подтвердились, журнал сохраняет обе причины. Ошибка очистки не заменяет первичный результат. Такой сеанс не считается успешно восстановленным: перед новым запуском проверь, что собственная тестовая игра закрыта.

EN: If a new DLL fails observation and its stop/rollback also fails, diagnostics retain both causes. Cleanup failure does not replace the initial result, and recovery is not reported as successful. Confirm the owned test game has exited before another launch.

### Ввод команд / Command input

Открой «Логи», затем вкладку «Команды». Ввод находится снизу; отправленная команда и её ответ добавляются в конец вывода над ним. Прокрути вывод вверх, чтобы прочитать предыдущие ответы.

| Действие / Action | Как / How |
| --- | --- |
| Список команд / List commands | `help` |
| Справка и пример / Help and example | `help army` или / or `army --help` |
| Выполнить ввод / Submit input | `Ctrl+Enter` или «Выполнить» / or Run |
| Подсказать команду или аргумент / Complete command or argument | `Tab`; для `army` — сначала герой, затем существо / for `army`: hero, then creature |
| Предыдущий ввод / Previous input | `↑`; `↓` возвращает черновик / restores the draft |
| Изменить ширину и высоту / Resize width and height | Перетащи край или угол панели / Drag a panel edge or corner |
| Продолжить игру / Resume playing | «Вернуться на карту» / Return to game |

RU: Когда читаешь старый вывод, поступающие ответы не должны сбрасывать прокрутку. Отправка новой команды возвращает к последнему ответу. Размер панели и история сохраняются при обновлении консоли.

EN: Input is at the bottom; submitted commands and responses append to the output above it. Scroll upward for earlier responses. Incoming responses preserve your position while reading older output; submitting a new command returns to the latest output. Console updates preserve panel geometry and history.

Historical snapshot (superseded where current acceptance is listed above): RU: Проверены выполнение, выбор подсказки, история и изменение размера. Отдельная проверка остаётся для изоляции колесика: прокрутка консоли работает, но автоматический тест не подтвердил зум камеры даже при скрытой панели. Поэтому он пока не доказывает, что колесико полностью отделено от камеры.

Historical snapshot (superseded where current acceptance is listed above): EN: Submission, completion selection, history and resizing have been checked. Wheel isolation remains open: console scrolling works, but the automated test could not establish camera zoom with the panel hidden, so it cannot prove full isolation.

## Автообновление консоли / Automatic console updates — 2026-10-05

RU, уточнение владельца: консоль теперь панель внутри окна игры. «Вернуться на карту» скрывает её; «Логи» открывает снова. Окно карты больше не отключается глобально. Внешний вид проверен в живой игре, физический ввод и возврат ещё проходят контроль; прежние описания отдельного desktop-окна относятся к предыдущему прототипу.

EN: Console now appears as a panel inside the game. Return to game hides it; Logs reopens it. Game HWND is no longer globally disabled. Live appearance passed; physical input/return remain under acceptance. Earlier desktop-popup notes describe the previous prototype.

RU: `xkit start NAME --background` просит Windows показать тестовую игру без активации при запуске. Код CLI и помощника собран; пока не подтверждено, что сама игра не заберёт фокус позднее. Не считать этот флаг доказательством экономии GPU.

EN: `xkit start NAME --background` requests a window without activation at startup. CLI/helper build checks passed; the game's later focus behavior remains unverified. The flag alone does not prove reduced GPU load.

RU: Брокер сохраняет непрочитанный результат до подтверждения получения интерфейсом. Потеря ответа при опросе не запускает команду заново и не удаляет её из ожидающих. Проверки очереди и передачи состояния прошли. Живой перенос уже готового непрочитанного ответа через замену DLL проверен без повторного выполнения; перенос ещё выполняющейся долгой команды остаётся отдельной проверкой.

EN: Unread results stay protected until UI delivery acknowledgement. Lost polling replies neither replay commands nor discard pending IDs. Queue/state checks and live completed-but-unread result handoff passed without replay. A long command still executing during DLL replacement remains unverified.

Historical snapshot (superseded where current acceptance is listed above): RU: В коде подготовлено сохранение прокрутки журналов и результатов при HMR. Если читаешь старые строки, новый результат не должен автоматически возвращать вниз. Передача состояния и сборка проверены; поведение прокрутки в живом интерфейсе ещё требует приёмки.

Historical snapshot (superseded where current acceptance is listed above): EN: Log/result scroll and bottom-follow state can checkpoint; new output respects reading older lines. Build/transport checks passed; live scrolling behavior remains unaccepted.

RU: Положение и размер панели сохраняются при HMR. В живой игре проверены размеры 600×400 и 920×600, обновление графического буфера и точное восстановление геометрии после замены DLL. Перетаскивание границы мышью проверено 2026-10-07; перенос между мониторами ещё не проверен.

EN: Panel position and size survive HMR. Live checks verify 600×400 and 920×600 Win32 resizing, graphics-buffer reset and exact rectangle restoration after DLL replacement. Physical edge dragging was verified on 2026-10-07; cross-monitor migration remains unverified.

RU: Рядом с каждым новым поколением `XalKitConsole.dll` сборщик сохраняет `XalKitConsole.LICENSES.txt` с полными уведомлениями Dear ImGui, ImTerm, ImGuiColorTextEdit и nlohmann JSON. При распространении консоли сохраняй этот файл рядом с DLL. Готовое поколение без уведомлений не выдаётся. Версии библиотек закреплены в `native/console_module.cmake`.

EN: Every new frozen console generation includes `XalKitConsole.LICENSES.txt` with complete notices for the four pinned libraries. Keep it beside the console DLL when distributing it. Missing notices prevent a ready generation. Library versions are pinned in `native/console_module.cmake`.

RU: Фоновая CPU-квота проверена настоящим Windows API на лёгком дочернем процессе, включая снятие ограничения. Влияние на видеокарту игры и отзывчивость SDK ещё не приняты.

EN: Background CPU quota and reset passed a real Windows API check on a lightweight child. Game GPU impact and SDK responsiveness remain unverified.

RU: После жалобы на нагрузку добавлен экономичный фон для нативного сеанса: CPU-бюджет H5 включается, когда работа идёт в другом приложении, и снимается при возврате в игру/консоль. Актуальный эффект на GPU ещё требует замера. Сборка и первоначальный запуск не входят в этот фоновый лимит. Ctrl+C закрывает тестовую игру и освобождает её ресурсы.

EN: Native sessions now have a source-stage background CPU budget after plugin readiness, released when returning to game/console. GPU impact remains unverified; compilation/initial startup are outside this limit. Ctrl+C closes the test game and releases its resources.

RU: В нативном сеансе `xkit start NAME` консоль подключена по умолчанию. Изменение её C++-исходников и переводов автоматически собирает новую DLL и заменяет интерфейс в работающей игре. Редактор, история и результаты сохраняются; старые команды не выполняются повторно. При ошибке компиляции рабочая версия остаётся. Исправь файл и сохрани его для следующей попытки. В терминале появится «Консоль обновлена». Завершение сеанса — Ctrl+C.

EN: Native `xkit start NAME` includes the console by default. Saving console C++ sources or translations builds and replaces its DLL in the same game process. Editor/history/results survive; old commands are not replayed. Compiler failure preserves the working version; fix and save the file to retry. Ctrl+C ends the session.

RU: Обновление числа starter-плагина после замены ядра отправляется, когда закончена общая замена всех плагинов. После подтверждённого отката интерфейс обновляется таким же способом. Промежуточное сообщение об обновлении одного ядра не запускает это действие.

EN: Starter display refresh waits for the complete multi-plugin core transaction, or confirmed rollback. An individual worker's core-applied event does not trigger an early display request.

RU/EN: Live source edit→build→apply, compiler failure and recovery passed in owned36324; process/monitor closed0. Observed edit-to-apply7.92s, build7.56s, replacement0.26s. This native-session check does not cover resource-session distribution, pending-command transfer, tab/geometry/scroll preservation, full input acceptance or publication review. Earlier dated prototype notes below are historical.

## Готовые нативные пакеты / Native player packages

### В разработке: владелец callback справочника / In development: bank callback owner

RU, 2026-10-07: `native/selector_runtime.hpp` предоставляет `SelectorRuntime` для доверенного сгенерированного x86-кода. `Initialize` создаёт сохраняемые данные, `Replace` подготавливает и проверяет новую область кода, `Invoke` вызывает callback, `Stop` убирает код, оставляя данные для повторного подключения. Методы выполняются последовательно: замена ждёт завершения текущего вызова. Повреждённые поправки адресов отклоняются до переключения, рабочий код сохраняется. Callback принимает публичную модель объекта и возвращает выбранное окно через обычный C++ контракт.

EN: `SelectorRuntime` owns trusted generated x86 callback memory and retained data. Initialize data once, prepare a replacement with `Replace`, call through `Invoke`, and remove code with `Stop`. Stop preserves data for readdition. Operations serialize; replacement waits for an active callback. Invalid relocation bounds reject a candidate before activation and keep the previous code. The callback uses an ordinary C++ argument/result contract.

RU: локальный прототип `Heroes5BankSelectorControl` в общем загрузчике владеет одним контекстом справочника на время процесса. Запросы `SelectorRequest` имеют проверяемые размер и версию; входные буферы копируются. После инициализации загрузчик закреплён до завершения процесса, поэтому освобождение ссылки потребителя на DLL не уничтожает контекст. `Stop` освобождает код, а данные сохраняются для следующего подключения. Это внутренний контракт доверенного кода SDK, не команда игрока и не проверка прав на чужой процесс.

EN: The local `Heroes5BankSelectorControl` prototype in the shared loader owns one bank selector context for the process lifetime. Versioned `SelectorRequest` buffers are copied; no caller buffer is retained. Initialization pins the loader until process exit, so consumer DLL-reference release does not destroy state. Stop retires code while retaining data. This is a trusted in-process SDK contract, not a player command or foreign-process authorization. The native forwarding fixture verifies code/data copies, consumer reference release/reacquisition, new code, retained data and stop.

RU: `Status` возвращает текущее поколение. Остальные запросы передают его как `expectedGeneration`; несовпадение отклоняет запрос до действия. Успешные инициализация, замена и остановка повышают поколение, отказ кандидата оставляет его прежним. Это защита от устаревшего контроллера, а не авторизация кода внутри процесса. Нативный тест проверяет отказы старых Stop/Invoke/Replace и сохранение рабочей версии после повреждённого кандидата.

EN: Read the current generation through Status and supply it as `expectedGeneration` for other requests. Stale requests fail before action. Successful initialization, replacement and stop advance the generation; a rejected candidate does not. This prevents stale-controller operations, not unauthorized code inside the same process. Native tests cover stale stop/invoke/replace and candidate rejection preserving current code and generation.

RU/EN: Invoke refuses an unclassified startup and a foreign thread without changing output or generation. For the game it uses the thread recorded after build verification in DirectInput startup; the synthetic non-game forwarding host uses the state-initialization thread. `BindWindow` checks the window's process, game class, visibility and thread, then advances generation on first binding. Game invocation rechecks that binding. Native tests verify non-game binding refusal and foreign-thread invocation refusal. The 2026-10-07 owned-game test confirmed the startup/window thread match through the resident service and normal exit0. No bank hook or callback swap was exercised by that test.

RU/EN: Native fixtures verify eight replacements, rejection rollback, stop/readd and active-call serialization. This is local development work, not part of the published SDK release. BankLayout AttachHook/DetachHook now use statically linked MinHook from a hash-pinned source revision; manual instruction writes were removed. A synthetic worker calls its target during eight queued enable/disable cycles; original bytes and results are checked. Stop refuses an active hook; uncertain library application blocks further mutation and retains memory. Positive game-hook execution remains unverified. Resident owner/core integration and game UI reference cleanup are still incomplete. Do not place game pointers in the portable plugin snapshot or enable legacy plugin HMR from these fixtures alone.

RU: первая сборка исходников SDK скачивает небольшой закреплённый архив MinHook; повторные сборки используют кеш CMake. Игроку этот шаг не нужен: библиотека включена в общий загрузчик. Нативный пакет содержит `NOTICE.txt` с полными уведомлениями MinHook и его дизассемблера. Исходная ревизия и хеш архива закреплены в CMake; сборка не выбирает текущую ветку библиотеки.

EN: The first SDK source build downloads a small pinned MinHook archive; subsequent builds reuse CMake's cache. Players receive it inside the shared loader and need no separate library installation. Native ZIPs include NOTICE.txt with complete MinHook/disassembler notices. CMake pins both source revision and archive digest, not a moving branch.

RU: `xkit release NAME` создаёт ZIP с одной DLL плагина и общими `bin/dinput8.dll`, `bin/d3d9.dll`. Распакуй архив в отдельную папку. Перед первой установкой переименуй исходный `bin/d3d9.dll` игры в `d3d9.universe.dll`, затем скопируй папку `bin` из пакета в игру. Уже сохранённый оригинал не заменяй. Сохрани резервную копию существующего `dinput8.dll`. У каждого мода своя DLL; общие файлы пакетов должны принадлежать одной версии SDK. Точный порядок установки и удаления есть в README.txt пакета.

EN: Release includes the shared input bootstrap and graphics facade with matching hashes. Extract separately; on first installation retain the game original as d3d9.universe.dll before copying package bin files. Preserve an existing retained original and back up the previous input bootstrap. Same-version packages share infrastructure and keep independent plugin DLLs. No original game DLL is redistributed. Players need neither a developer launcher nor a compiler; follow the package README.txt.

## Отдельная установка / Standalone installation

RU: SDK может храниться отдельно от папки проектов. Для нативных модов скачай его вместе с закреплённой зависимостью Game API; порядок установки указан в начале README. `xkit setup` сохраняет папки игры и проектов, поэтому команды работают из папки проекта. Не копируй скрипты SDK в каждый мод.

EN: A fresh isolated environment passed setup, doctor, new, build, release and diagnostics using a frozen current-source export with a complete independent Game API directory. Both a resource H5U and a separate native plugin ZIP were produced. All 111 exported source hashes remained unchanged. This verifies the local candidate workflow, not availability of uncommitted changes in a public clone, nor visible runtime effects.

Historical snapshot before ordinary-player acceptance: RU: визуальные проверки подтвердили число от выпущенной DLL и полную метку H5U в меню. Управляемый `xkit start` также прошёл обновление 42 → 43 → 42 и штатное закрытие после отрисовки. Ошибка обычного запуска вне SDK остаётся открытой. [Результаты проверок](https://xaaalera.github.io/heroes5-knowledge/reference/research-diary/).

Historical snapshot before ordinary-player acceptance: EN: If the SDK moves, its build cache automatically uses a separate directory when the earlier CMake cache belongs to another source location. Existing cache files are preserved; manual deletion is unnecessary.

## Готовность сеанса / Session readiness

RU: после `xkit start` сначала появляется «Собираю и подключаю плагины». Сообщение «HMR работает» означает, что выбранный плагин уже подключён. Наблюдение за файлами другого плагина ещё не означает готовность выбранного. Ошибка сборки остаётся в журнале с именем плагина и путём диагностики; исправь исходник и сохрани его для новой попытки.

EN: `xkit start` first reports that plugins are being built and connected. The HMR-ready message appears only after the selected plugin is applied. A different plugin or an active file watcher does not establish readiness. Build errors retain plugin identity and diagnostics; fix and save the source to retry.

Historical snapshot (superseded where current acceptance is listed above): RU/EN, 2026-10-05: fresh current-source `xkit check --player` passed in65.2s from a separate workspace: added functions/headers, core export, HMR/rollback, independent plugin removal/readd and two released DLLs. Both games exited0, callbacks removed and files restored. A missing test map is now prepared automatically; an existing map is preserved. This check does not establish map transitions, scene rendering or public availability of the candidate.

## Проверка запуска нативного проекта / Native project startup check

RU: `xkit start NAME` проверяет библиотеку анимации до её инициализации: файл, версию и размещение в памяти. При отказе созданная тестовая игра закрывается; исходная установка не меняется. Результат записывается в отчёт сеанса как `image_placement_verified`. Помощник запуска нужен только разработчику и не входит в готовый мод. Ресурсный H5U пока использует прежний путь запуска; эта проверка не требует от него C++-компилятора.

EN: Native `xkit start NAME` uses an owned suspended-child helper, validates process identity before preparation and checks the animation image at its loader event before initialization. A rejected launch terminates that owned child. Successful validation appears in the session report. The helper is a developer tool, never a player dependency. Resource sessions retain their existing compiler-free launch path. This control does not prove visible mod effects or fix every possible startup failure.



## Independent mod delivery / Независимая поставка модов

RU: Каждый мод выпускается и устанавливается отдельно: ресурсный — собственный H5U, нативный — собственная DLL. Общий загрузчик или инфраструктура SDK допустимы как описанная зависимость. Установка одного мода не требует остальных. Проверять отдельные пакеты и совместную установку. Загрузка и выгрузка по требованию во время игры требуют отдельной проверки.
EN: Release each resource mod as its own H5U and each native plugin as its own DLL. Do not merge several mods into one mandatory payload DLL. A shared loader or SDK infrastructure may be an explicit dependency. Installing one mod must not require the other mods. Verify separate packages and coexistence. Optional installation does not establish runtime demand loading/unloading; verify that capability separately.


## H5U resource delivery / Выпуск ресурсного H5U

RU: `xkit new NAME --resources`, затем `xkit build NAME` или `xkit release NAME`. Обе команды создают готовый H5U и build.json в папке выпуска проекта. При отсутствии подготовленной песочницы сборка готовит её без запуска игры; оригинальные PAK и бинарники не меняются. Установка игроку — один H5U в UserMODs, без Python, DLL или отчёта build.json.

EN: `build`, `release` and `start` accept an optional positional project name as documented; omission uses the selected project. The CLI previously treated NAME as an option and rejected the documented syntax; Typer Annotated arguments fix this while preserving direct Python default=None calls. Regressions check named selection and resource preparation without deploy/launch.

Installed resource-check new/build/release from outside the checkout passed file acceptance: exactly one menu version member, CRC/hash checks, original installed/sandbox source resource match, expected marker text, game binaries match and previous selected project restored. Artifact is `.local/xalkit/releases/resource-check/resource-check.h5u`. Proof xkit-h5u-release-verification.json; live visual acceptance=false. Python81 PASS. Do not transfer this file-only proof into a rendering claim.
## Owned crash monitoring / Монитор сбоев своего сеанса

RU: захват дампа проверен на отдельном диагностическом процессе, не на игре. Он ожидает явную команду, вызывает контрольное исключение и даёт проверить, что дамп содержит именно его PID, время создания и исключение. Этот исполняемый файл не входит в player-пакеты и обычные тесты CTest не запускают его падение.
EN: ProcDump may return a nonzero code after reaching its dump limit. The adapter reports `dump_captured` only when a complete minidump exception stream and process identity match the retained owner. Other nonzero outcomes remain failures. Capturing a game exception always fails game acceptance; a diagnostic capture is not a successful gameplay session.

Controlled fixture fatal-monitor-20261005T033327/report.json PASS: one135272-byte dump with the expected exception and owned PID/creation, monitor exit1 classified as capture, all processes finished,0.732s excluding compilation. Fixture exit2 occurred after debugger capture, so the check uses the exception stream rather than equating process exit with the exception code. Python79 PASS; regressions reject foreign identities, truncated structures, excessive parameter counts and out-of-file context. [Microsoft exception-stream format](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_exception_stream) and [process identity format](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_misc_info) define the inspected fields.

RU: общий `sdk_diagnostics.OwnedCrashMonitor` используется в сеансе разработки и обеих проверках SDK. Локальный ProcDump запускается только после проверки подписи Microsoft, целостности инструмента, живого PID/creation time, пути тестовой игры и четырёх хешей сборки. Отдельный handle удерживает идентичность процесса до остановки монитора. Подключения по имени процесса нет.

EN: The adapter delegates capture to Microsoft Sysinternals ProcDump. It retains the binary raw log and writes a readable UTF-8 version, including ProcDump's mixed ASCII/UTF-16 output. Nonzero tool exit is a monitoring failure; acceptance records cleanup errors instead of claiming success. Unconfirmed stop retains handles while the Python host is alive. Snapshot/report failures are best-effort and cannot skip later game/file cleanup.

Fresh installed HMR030310/player030346 monitored control passed60.364s, both tool exits0, no dumps and games exit0. Python78 PASS covers invalid signature/tool changes, wrong creation/exited process, uncertain stop, mixed logs and nonzero tool exit. Subsequent status/decoding fixes have unit coverage and corrected saved logs; no new fatal crash was induced. Development `xkit start` integration exists but has not received a fresh live check with this adapter. Automatic installation, capture before launch handshake, controlled fatal-dump verification and diagnosis of the prior breakpoint remain open.

## Check the SDK / Проверить SDK

```powershell
xkit check
xkit check --player
```

RU: сначала выполни `xkit setup` и закрой игру/редактор. Команда сама находит MSVC/CMake, готовит тестовую копию при необходимости и создаёт два временных плагина. Она проверяет события, обновление плагина и ядра без перезапуска, передачу состояния, откат и независимое отключение/повторное подключение. Исходники выбранного проекта не используются. Итог содержит путь к JSON-отчёту; подробный вывод сохранён в `.local/xalkit/check/logs/backend.log`.

EN: Installed xkit ran from outside the checkout, native control sdk-shared-dispatch-20261005T021817/report.json PASS. Full human command40.8s including canonical SDK preparation; backend32.980s. The owned game exited0 and subscriptions were cleared. Whole Python71/71 PASS after two CLI boundary regressions. Release packaging, graphics/map transitions and automatic crash capture remain separate acceptance scopes. Cancellation requests backend cleanup; a live cancellation control remains outstanding.

CLI check errors use Google RPC Status envelopes in the JSONL journal. Registry: `xalkit_ui.ERROR_REASONS`; messages use gettext RU/EN. `SDK_CHECK_FAILED` maps to INTERNAL/500 and points to diagnostics; `SDK_CHECK_CANCELLED` maps to CANCELLED/499. Raw backend/compiler failures stay in diagnostic artifacts. Compiler build failure is distinct from missing Build Tools.

RU: Ctrl+C запрашивает отмену и ждёт закрытия собственного сеанса. Остаток вывода backend сохраняется, итог содержит ссылку на отчёт об очистке. `SDK_CHECK_CLEANUP_UNCONFIRMED` означает, что завершение не доказано: проверь журнал перед новым запуском. Тайм-аут не считается выходом процесса и не вызывает повторный запуск.
EN: Fresh live cancellation022831 after active callbacks/core update has cancelled=true, passed=false, manager/game exit0, zero subscriptions and no cleanup errors. Installed CLI emits CANCELLED and links the report; full command33.942s/backend30.125s includes work before cancellation. Python72 PASS covers confirmed versus failed cleanup and retained final output. Early launch/ownership handoff and repeated interruption remain separate fault controls; this evidence does not prove every cancellation boundary.

RU: `--player` после успешного HMR собирает два пакета из его свежих исходников. Сверяет хеши SDK, Game API и принятые snapshots плагинов, затем создаёт вторую игру обычным `CreateProcessW`, без исправления импортов или стартового сценария в памяти. Карту запрашивает штатным аргументом `-advmap`; запрос сам по себе не подтверждает загрузку карты. Проверяет автоматическое подключение до диагностических клиентов, выполнение обоих обработчиков и независимую остановку. В конце закрывает игру и восстанавливает тестовые файлы. Совместный отчёт создаётся только после успеха обоих этапов.

EN: The player phase now uses ordinary CreateProcessW with no developer import or startup-script memory patches. The stock -advmap argument requests a map; the request alone is not proof that the map loaded. It still checks two released plugins, callbacks, independent shutdown and cleanup.

Historical snapshot (superseded where current acceptance is listed above): RU/EN, 2026-10-06 correction: Earlier combined PASS reports used native-probe.launch, which repaired import ordering even with control=False. They prove released DLL execution after developer preparation, not unmodified ordinary startup. The corrected checker has targeted launch tests; a fresh live pass remains required. Recent preload-only ordinary controls failed or stayed alive without a game window; startup reliability remains open.

EN: Canonical `plugin-player-check.py` has explicit live/report/toolchain/binary inputs and no hardcoded historical journal path. Cleanup attempts game, monitor and every staged file independently; errors remain in the final report. Changed external files are retained. If the local Microsoft ProcDump tool is present, the CLI passes its path; this optional lookup is not automatic installation or proof that every crash is captured.

Fresh installed combined check024633/024707 PASS,58.621s, both game exits0 and player sandbox restored. Source guards and cleanup were corrected after a bounded independent review; both major findings closed. A failed player phase cannot promote the successful HMR phase into a combined pass. Regressions cover API drift and continued restoration after one failure. Full graphics/map/H5U and player-phase cancellation acceptance remain separate.

## Script observers with game commands / Наблюдатели вместе с игровыми командами

RU/EN, 2026-10-06: added stock-console export now has explicit HMR proof. A frozen baseline lacks the named export; saving current source in the existing watcher automatically rebuilds/applies it in the same owned game, preserving plugin counter7 and advancing generation. Save→confirmed apply4.229s; new stock command succeeds, watcher/game exit0. The fixture is an ignored acceptance snapshot, not another active SDK checkout. Arbitrary engine calls, physical input and ordinary player startup are not certified by this control.

### Load a map / Загрузить карту

RU: `restart <имя>` повторно загружает указанную карту и сбрасывает её игровой прогресс. Имя обязательно: команда не угадывает текущую карту. `menu` запрашивает главное меню. Оба действия сохраняют SDK и плагины; стартовый скрипт карты может завершиться позже возврата обработчика. В консоли SDK те же команды: `restart WorkshopPolygon` и `menu`.
EN: restart requires an explicit map name and reloads its game state. menu requests the main menu. SDK/plugins remain active; map startup scripts may finish after dispatch returns. The embedded console uses the same commands.

Historical snapshot (superseded where current acceptance is listed above): RU/EN, 2026-10-06 follow-up: one-process live flow reset the modified resource, observed the map startup script, captured the actual menu, returned to the map and read all8 heroes. Payloadcounter7/gen1 retained; game/controller0/full15.973s. These CLI flows are verified; physical console entry/new-export HMR/ordinary startup remain open. Supersedes restart/menu-pending below.

В открытом сеансе SDK:

```powershell
xkit game map WorkshopPolygon
xkit game restart WorkshopPolygon
xkit game menu
```

RU: имя берётся из Maps песочницы; Tab дополняет имена файлов. Та же команда `map WorkshopPolygon` доступна в консоли SDK через общий реестр. Справка: `xkit game map --help`. Обычный ответ — «Загрузка карты … запрошена»; `--json` возвращает receipt с `dispatch_returned=true` и `effect_verified=false`. Возврат обработчика не доказывает завершение загрузки. Команда использует уже подключённое ядро и не останавливает плагины при завершении своего клиента.

EN: Run this in a connected SDK session. Tab completes sandbox map filenames; the embedded console uses the same registry (`map WorkshopPolygon`). The normal response says loading was requested. JSON distinguishes dispatcher return from verified game effect. The command borrows the resident core without stopping plugins when its client exits.

RU/EN, 2026-10-06 correction: public map command is now connected, superseding the prototype-only limitation below. Installed CLI loaded WorkshopPolygon, returned the expected eight heroes and preserved active payload counter7/generation1. Game/controller exits0, full10.374s. Shared routing/help/completion/display have targeted tests. Physical console entry, new-export HMR and independent current-archive startup remain separate gates. Repeat map with an explicit filename to load it again; restart-current-map and return-to-menu are still separate backlog items.

RU, 2026-10-06: подготовлен native прототип вызова штатных консольных команд на основном потоке. Подтверждены изменение настройки и переход из меню на WorkshopPolygon с отдельным чтением героев. Пользовательские команды загрузки/перезапуска карты ещё не подключены к этому пути; внутренний диагностический клиент не является новым способом работы для пользователя. Следующий этап — безопасное использование уже подключённого ядра без остановки других плагинов, затем проверка CLI/консоли и HMR нового export.
EN: A native prototype invokes stock console commands on the game thread. Setting changes and a menu-to-WorkshopPolygon transition with a separate hero read are verified. Public map-load/restart commands are not wired to this path yet. Next: borrow the resident core without stopping plugins, then verify human CLI/console flows and HMR of the added export.

RU, 2026-10-06: ядро SDK теперь распознаёт основное окно меню Universe. Оно выбирает единственное подходящее окно своего процесса; служебные, скрытые и неоднозначные окна отклоняются. Подключение ядра к реальному меню и штатное закрытие проверены отдельно. Консоль в меню и переход на карту требуют нового живого контроля; этот результат не подтверждает обычный запуск готовых DLL.
EN: The SDK core now recognizes the Universe menu window and requires one eligible owned window. Auxiliary, hidden and ambiguous windows are rejected. Core connection and normal game exit passed on a real menu; console-in-menu and transition-to-map acceptance remain separate. This does not certify ordinary released-DLL startup.

RU: консоль теперь сохраняет последний результат ошибки и показывает её сообщение; ошибка не считается успехом. Подключение панели к меню проверено, но игровые команды в меню пока не прошли приёмку: запрос героев может ждать до тайм-аута. `xkit check --console` проверяет семь сценариев на тестовой карте с героем, а не произвольное меню. Новый результат после исправления не переносится на остальные открытые проверки.
EN: The console now retains the latest public error outcome and displays its message without treating it as success. Panel connection to the menu is verified, but menu game commands remain unaccepted: hero queries can time out. The seven xkit check --console cases use the authored map and hero, not an arbitrary menu. Other open acceptance gates remain separate.

RU: в новом сеансе `xkit start` почтовый ящик игровых команд может работать одновременно с несколькими SDK-наблюдателями диспетчера. Для описания поддерживаемой точки используй имена `h5::hooks::ScriptDispatchCall` и `ScriptDispatchTarget` из общей Game API. SDK сам регистрирует подписку плагина и снимает её при обновлении или отключении. Прямые перехваты из payload запрещены.

EN: The shared mailbox owner has64 callback slots. It preserves caller machine state and invokes trusted, non-reentrant callbacks on the validated game thread. SDK core replacement removes old callbacks before state transfer and restores subscriptions only after candidate activation. Unknown/modified detours and incompatible contracts are rejected. A callback is an observation point; arbitrary world-transition calls are still unverified.

Fresh owned live sdk-shared-dispatch-20261005T010236/report.json PASS: two simultaneous observers with Lua hero reads, alpha payload3→4, automatic core1→2, rejected core3 and coordinated rollback, independent removal/re-add, same PID/creation, zero subscriptions after stop, manager/game exit0. Full38.330s; three mailbox reads0.063–0.078s. Runtime hashes match. SDK native4/API native1/Python68 PASS; checker cleanup fault paths have separate unit coverage after this live run.

RU: Для повторной проверки используй `xkit check`; для независимых готовых DLL — `xkit check --player`. Вспомогательный `plugin-control-check.py` остаётся внутренним инструментом; вручную собирать его длинную команду не требуется. Проверка переходов карт и отображения сцены выполняется отдельно.

EN: Use `xkit check` for repeat acceptance and `xkit check --player` for independent ready DLLs. `plugin-control-check.py` remains an internal helper; the installed CLI supplies its arguments. Map transitions and scene rendering are checked separately.

RU: нативный общий диспетчер теперь создаётся автоматически и в готовых DLL-модах. Он остаётся в процессе до выхода игры; остановка всех плагинов очищает подписки, а исходный вызов продолжает работать. Стенд проверяет число подписок и выполнение callbacks, а не требует возврата исходных байтов после последнего плагина. Неизвестные перехваты по-прежнему отклоняются.

EN: Native exit no longer depends on screenshot/OCR confirmation. The guarded bridge queues the existing exit request on the game thread through a dedicated message. The owned diagnostic client reports `game_exited` and `exit_code` only after its retained process handle signals exit, then avoids remote Stop/free calls against the dead process. Old bridge exports remain compatible; unavailable exit returns the existing unsupported status.

Historical native control014926 and player control020619 verified their dated sources and scopes. Current repeat entry is `xkit check --player`; use its fresh report to judge current code. The earlier startup breakpoint was not reproduced by ProcDump monitoring. Scene rendering and map transitions remain separate checks.

## Plugin membership during core updates / Состав плагинов при обновлении ядра

RU: общая замена ядра сохраняет состав участников до подтверждённого завершения или отката. Удаление папки/последнего C++ файла отключает плагин после операции; новый проект подключается после неё с принятой версией ядра. Индивидуальные команды в этот короткий промежуток отклоняются: дождись результата и повтори команду. Общая остановка сеанса остаётся доступной.

EN: Core updates retain participant instance identities. A reply from an earlier instance cannot acknowledge the current update. Losing any participant, including one already updated or still waiting, stops the manager with an unconfirmed-cleanup error; it never reports successful completion. Close the owned test game before restarting after such a failure. Source removal and new-plugin discovery wait until the transaction settles.

Verification: Python66/66 PASS. Five game-free scenarios cover acceptance, confirmed rollback, lost queued/applied workers and stale instance replies. Reverting the membership protections makes the regression fail. These unit checks do not establish live-game hook compatibility.

RU/EN: Real native worker control sdk-membership-20261005T004438/report.json also passed: removal after the first core acknowledgement, deferred addition with core2, rejected core3 with coordinated rollback and retained states10/30. Full27.115s, supervisor exit0. This used local native processes and no game. Game-control and SDK script observers still compete for the same hook; a common subscription owner and fresh combined live control remain required.

## Background commands and map transition research / Команды в фоне и переходы карты

RU: `xkit start` включает штатную настройку фонового обновления только в выбранном профиле тестовой копии. В установленной игре настройки не меняются. Контроль без мыши/фокуса: пять чтений ресурса0.062–0.083s и ещё три0.063–0.095s в свежих процессах прошли. Это устраняет зависимость обычного терминального сеанса от активности окна в этих проверках; другие переходные состояния ещё требуют контроля.

EN: Cleanup now uses the existing guarded native exit command when control PID/creation matches the immutable owner snapshot. It records reason/exit code and does not resubmit an unconfirmed native request. GUI close remains the fallback without a matching controller. Fresh PID23952 exited normally via native_exit with exit_code0. No original installation files were changed.

RU/EN: Shipped Nival CheatCodes documents advmap; stock UI resources identify restart_mission. Inline advmap in PID49588 changed hero roster but scene capture became blank and a later mailbox request stayed pending. Stock reaction tests did not establish a reset of the resource marker. These are research outcomes, not finished restart/load commands. Pending status1 means the hook has not consumed the request, not proven Lua execution deadlock. Some scene captures returned after disabling/readding the diagnostic HUD; later captures were still blank without it, so HUD is not a proven sole cause. Background settings, delivery phase and capture path must be tested separately.

EN: Frame validation now rejects few-colour HUD-only output as blank, with a regression for three-colour pseudo-content and a varied real-content fixture. Do not treat rendered controls as proof that the underlying game scene is captured. Research artifacts: xkit-map-load-probe.json, xkit-background-profile-check.json, xkit-background-profile-final.json. Same-process restart/load remains open; no invented command is advertised.

## Human command entry / Команда для человека

RU: Все `xkit game` команды записывают начало и результат/ошибку одной операции в общий SDK events.jsonl, включая длительность. Журнальные сообщения идут в stderr, JSON-ответ остаётся в stdout. При сбое выводится понятная подсказка о тестовом сеансе и состоянии игры; исходная техническая причина и traceback сохраняются в журнале. Это связывает события одной команды, но ещё не делает внутренние standalone скрипты участниками общей корреляции сеанса.

RU: `xkit start` для H5U собирает и устанавливает пакет в тестовую копию, затем удерживает управляемый сеанс игры. Ctrl+C закрывает только собственную игру и её монитор. C++-компилятор и supervisor DLL-плагинов для этого пути не вызываются. Изменения ресурсов применяются после остановки, новой сборки и запуска. Итог native/H5U сеанса записывается в session-latest.json; `xkit diagnostics` показывает его путь. Идентификатор владельца в отчёте сохраняется точно.

EN: Shared start_session handles native/resource ownership, monitor, stop and failure reporting. Resource unit path asserts no native compiler/supervisor. Installed H5U Ctrl+C live control exited game/monitor0, retained lifecycle JSON; no visual acceptance. Subsequent report-write failure correction preserves original errors and closes EventLog, covered by fault-injection regression and bounded reviewer recheck. Python86 PASS. Resource package remains in the sandbox after deployment; the original installation is untouched.

RU: После первого Ctrl+C SDK завершает сеанс. Повторные Ctrl+C во время очистки не прерывают ожидание supervisor, закрытие собственной игры и остановку монитора. После очистки прежние обработчики сигналов восстанавливаются. Проверено тремя Control-C через терминал VS Code; команда Terminate Task и закрытие редактора в этот контроль не входят.

RU: Задача start проверена в настоящем VS Code: сохранение новой функции обновило плагин в том же процессе игры, Ctrl+C в терминале завершил SDK и закрыл тестовую игру с монитором. Исходник и пользовательские настройки восстановлены. Закрытие редактора или команда Terminate Task этим контролем не проверялись. Материалы: actual-ide-hmr-verification.json.

RU: `xkit new` создаёт `.vscode/tasks.json` для открываемой папки проекта: start/build/release/diagnostics. Каждая команда работает через стандартную process-задачу, использует явное имя проекта и его workspace, сборка назначена на Ctrl+Shift+B. Нативные ошибки распознаёт декларативный matcher стандартного формата VS Code с абсолютными путями; отдельное C++-расширение не требуется. H5U задачи matcher не получают. Перезапусти редактор после установки xkit, если его PATH ещё не обновился. Старые проекты автоматически не переписываются.

EN: Isolated VS Code1.140.0 Extension Host fetched four generated tasks, ran a failed native build, populated the actual Problems diagnostic model with C2065 at line23/column15 and ran successful recovery. Initial gcc base matcher was unavailable in a clean editor; replaced with standard inline configuration. CMake/MSBuild capture now uses UTF8 explicitly, fixing Cyrillic messages. Proof actual-ide-task-verification.json, Python86 PASS, source/user config restored. Resource task command retains its separate proof. HMR/editor-menu termination remains unverified; no game launched in this IDE control.

RU: Если запуск отменён до продолжения игры, SDK завершает только созданный им ещё не запущенный процесс и ждёт подтверждения выхода. Такая отмена отмечается отдельно; она не считается падением уже работающей игры. Живой контроль использовал прерывание в callback подготовки, а не многократное нажатие Ctrl+C в терминале.

RU: SDK сохраняет владельца тестовой игры до продолжения её запуска. При подготовке DLL-проверки весь набор исходников ядра сверяется с последней успешно принятой версией; одинаковые изменённые файлы в двух пакетах не считаются подтверждением. Новый живой check --player прошёл63.161s, затем start подтвердил порядок сохранения владельца и штатное завершение. Перехват сбоя до продолжения запуска ещё не подтверждён: монитор подключается после него.

RU: `xkit check --player` теперь также проверяет добавление нового C++ файла, функции и заголовка плагина, изменение заголовка, новый export ядра, сохранение функциональности после отката и в готовых DLL. Расширенный живой прогон прошёл; исходники отчётов повторно проверены. Материалы: xkit-extended-player-verification.json. Старый контроль045259 ниже описывает свой прежний объём проверки.

EN: Fresh installed check --player on current sources passed56.811s: native045259 and player045332 reports, both game/manager/monitor exits0, no dumps/cleanup errors, zero callbacks and restored player staging. Canonical source hashes in both reports revalidated. This covers observer/core lifecycle and same-source delivery; adding functions/exports remains the separate extended plugin-check scope. Private retained pointer: xkit-current-source-player-verification.json. Python85, existing SDK/API native test builds4/1 PASS.

### Найти причину сбоя / Find diagnostic files

RU: При ошибке собственного сеанса `xkit start` сохраняет доступные Application Error события Windows. Запись принимается только при совпадении PID, времени создания и пути игры. `xkit diagnostics` показывает путь к этому отчёту, имя модуля и время события; адрес сбоя не выводится. Чтение ограничено последними128 событиями после создания процесса. Событие Windows может появиться с задержкой; пустой результат не доказывает отсутствие сбоя. Ошибка чтения системного журнала сохраняется отдельно и не заменяет исходную ошибку сеанса.

EN: Failed human start attempts to retain matching Windows Application Error events. PID, creation time and executable path must all match. Diagnostics shows module/time and report path, not fault addresses. The query is bounded to128 events after process creation; delayed or unavailable Windows logging can produce no match. Original session failure remains primary. Real retained granny startup event was retrieved and displayed by installed diagnostics; Python86 PASS. Automatic collection is unit-covered, not a newly induced game crash.

Запусти `xkit diagnostics`. Команда показывает журнал событий разработки, отчёт и вывод последней проверки SDK, журнал последнего монитора и дампы в его папке. Она не запускает игру. Открой журнал по напечатанному пути; события ошибок содержат причину и этап операции. `xkit diagnostics --json` выдаёт те же пути для IDE и скриптов. Отчёт старого прогона относится к указанным в нём исходникам.

Монитор сбоев необязателен для разработки и не нужен игроку с готовым модом. Чтобы включить его:

1. Скачай архив с [официальной страницы Microsoft ProcDump](https://learn.microsoft.com/en-us/sysinternals/downloads/procdump).
2. Распакуй `procdump.exe`. Запусти `xkit diagnostics`: он напечатает путь, куда положить этот файл в рабочей папке.
3. Запусти `xkit start` или `xkit check`. SDK проверит подпись Microsoft и подключит монитор к собственной тестовой игре. После завершения снова вызови `xkit diagnostics`, чтобы найти материалы.

Наличие файла не означает успешную проверку подписи: это явно отмечено в выводе diagnostics. Журнал ProcDump показывает события процесса; дамп сохраняет состояние при исключении для дальнейшего разбора. Команда diagnostics не анализирует дамп и не определяет причину сбоя автоматически. Показывается последняя папка монитора, а не все исторические сбои.

EN: `xkit diagnostics` lists existing SDK artifact paths without launching or attaching to a process. `--json` supports IDEs/scripts. For optional crash monitoring, download ProcDump from the official Microsoft page, extract procdump.exe to the path printed by diagnostics, then run start/check. The launch adapter verifies the Microsoft signature. File discovery alone does not verify it. The command lists the latest monitor directory, does not analyze dumps and does not certify an older check against current sources. Installed outside-checkout human/JSON/help controls passed; Python85 PASS.

RU: При сбое игры или неподтверждённой остановке SDK сообщает об ошибке сеанса и показывает папку журнала. Ошибка остановки supervisor не отменяет попытку закрыть тестовую игру и монитор. Перед новым запуском закрой оставшуюся тестовую игру, если она есть. Сохранённый дамп означает ошибку этого сеанса, даже если монитор успешно завершился.

EN: Six negative mock scenarios cover game/supervisor exit, supervisor timeout, captured dump and monitor failure; Python83 PASS. Fresh installed active Ctrl+C verified normal game/monitor exit0 with final source and no leftover processes. PTY PowerShell interruption returned1; it is not claimed as frontend exit0. Private proof: xkit-session-failure-verification.json.

RU: Если закрыть тестовую игру раньше терминального сеанса, SDK отправляет остановку supervisor и дожидается его завершения. Повторный запрос закрытия уже завершившейся игре не отправляется. Локальный монитор сбоев, если установлен, сохраняет журнал и завершает работу.

EN: Fresh installed start verified the local crash monitor, source saves88→89→88 in one owned process and normal game-first exit. A second fresh run verified final cleanup without duplicate quit or false warning. Python83 PASS; private xkit-start-monitor-verification.json. First run predates the terminal-process cleanup correction. Scene rendering and fatal game capture remain unverified.

Primary command: `xkit`; XalKit is the product name in its annotation. `xal-kit` and `xalKit` are installed aliases. Typer owns parsing/help/completion; standard project.scripts + uv editable tool installation owns executable/environment discovery. [Install and daily commands](../README.md#xkit-команда-xkit--command-xkit). There is no custom shell/parser/translation engine.

RU: `xkit setup` сохраняет папки один раз; `new NAME` создаёт C++ проект в workspace/plugins, `new NAME --resources` создаёт H5U в workspace/mods. Выбранный проект сохраняется, поэтому `start`, `build`, `release` вызываются без длинных путей. Native start сначала собирает canonical ядро/загрузчик, затем запускает собственную видимую игру и supervisor, применяет passive display event только к нашему starter и закрывает свой процесс при завершении. Managed discovery исключает старые немигрированные плагины из общей папки. Стандартная очередь между reader thread/main thread устраняет ожидание stdout при Ctrl+C; supervisor process group защищён от преждевременного сигнала.

EN: Installed xkit executed from outside the checkout, settings/new/doctor and CMake build passed. Typer/Gettext tests and full Python65 PASS. Human-entry live session showed numeric42 then77 in the same owned PID51040 and closed normally. Rendered pixel gate was not established in this entry test: its standalone screenshot helper lacked H5_WORKSPACE and correctly refused the mismatched sandbox path. Prior full202146/205158 controls retain their own verified rendering/release scopes. Latest start now enables the game-control mailbox; that addition needs a fresh live check. Do not promote the initial entry test into full product acceptance.

Historical snapshot (superseded where current acceptance is listed above): RU/EN: `xkit game` exposes status/heroes/army/resource/teleport/level/interact over existing guarded game_control. Restarts/new map loading in the same process and hero/creature completion remain unfinished. Typer supplies command/option completion plus managed project names for start/build/release and map names for start --map. Run `xkit --install-completion powershell`, then open a new terminal. Actual fresh PowerShell with its installed profile completed `xkit release reso` to resource-check; isolated TabExpansion2 also covered start/build. Private proof: installed-completion-verification.json. Project enumeration filters incomplete/unmanaged directories and sorts/deduplicates results; Python82 PASS. Languages use standard GNU gettext with locale/{ru,en}/LC_MESSAGES/xalkit.po/.mo; Babel tooling belongs to requirements-dev, no Babel runtime dependency is required. `xkit language en|ru` persists the choice; XALKIT_LANG is the explicit environment override.

## Extended core in player releases / Расширенное ядро в выпуске

RU: выпуск теперь собирается из того же CMake target `heroes5_plugin_bridge`, который используется для HMR. Дополнительные исходники ядра, параметры компиляции и библиотеки входят в DLL игрока. Для выбранного ядра передай `--core-source <directory>` при выпуске; без этого используется `native`. В `release.json` добавлены `core_source_hashes` и хеш шаблона выпуска. Игроку по-прежнему достаточно обычного запуска Heroes/Lobby: Python и контроллер в архив не входят.

EN: Standard CMake target properties feed native/player_plugin.cmake, which combines the selected core sources with the payload and enables player startup. Compilation uses a standard temporary directory to keep MSBuild tracking paths short; generated project, output DLL, manifest and structured logs remain under the selected output. Shared game-api headers now participate in canonical core snapshots; core output cannot be inside its source tree.

Final control sdk-acceptance-20261004T205158/report.json PASS: automatic core update while the engine CALL observer is active, surviving beta state22, extra core helper99 retained through removal/readd and release. Both player packages match live payload/core source hashes. Ordinary game startup loads both; owned export probes return99 from each release DLL. Full102.970s; release builds6.418/6.446s, Python61 PASS. DevelopmentPID49352/releasePID24272 close normally, deployment restored. The export probes deliberately stop each test plugin before closing the owned game, after rendered UI capture. All seven reported SDK source hashes match this control. This supersedes202146 for the latest source revision, not historical timings.

RU/EN: Overall goal remains open: human entry/setup/new/dev/doctor/release, consistent diagnostics across other SDK tools, actual IDE task validation, transaction membership/uncertain-call hardening, standalone automatic-mode control and independent review still need completion.

## Readable and structured logs / Понятные и структурированные журналы

### Нативная шина: прототип / Native bus prototype

RU: `xkit game trace` читает системные сообщения текущего сеанса SDK. `--level warning` выбирает warning/error, `--level error` — только error; `--after N` продолжает чтение, `--limit N` задаёт размер страницы, `--json` возвращает машинный ответ. По умолчанию вывод читаемый и сохраняет имена модулей. Чтение использует уже загруженное ядро и не останавливает плагины.

EN: The managed SDK session pins the resident diagnostic endpoint and its binaries. Owner/path/hash mismatches refuse attachment; query buffers use a versioned bounded POD record. `--console` currently submits messages via Lua print with acknowledgements; that establishes script completion, not visibility of the built-in game console. This is not the planned standalone SDK console UI.

RU/EN, 2026-10-05: live two-module reads and severity changes after payload HMR passed in the same game; warning/error filtering and per-line script acknowledgements passed. Game exit0 and source/config restoration were verified. Native6/6 and Python107 PASS; bounded review's cross-session forwarding issue was corrected with a frozen-owner check before submission.

### Консоль SDK: план / SDK console plan

RU/EN, latest prototype: external CLI command request/completion messages visibly appear in the separate console. Module lifecycle states are derived from SDK discovery/application/stop events for the active session; an unsuccessful new build preserves the earlier active state. These adapters pass tests and the external command mirror passed a real game control with normal exit0. The module tab and GUI command input, completion/scroll and layout still need real acceptance. Console disconnection now publishes stopped status before destroying windows; game shutdown does not depend on console readiness.

RU/EN, current prototype: the console now obtains command descriptions and arguments from the existing CLI registration and sends jobs to the same backend. Queueing is asynchronous; repeated operation IDs are observed without resubmitting the action. Session ownership and disconnect cancellation are checked before remote writes. This path compiled and passed protocol/callback tests but has no new in-game command/input acceptance yet. External CLI mirroring and module lifecycle remain pending. SDK waits for actual console ready status and rejects initialization failure; default automatic attachment remains unchanged.

RU/EN, 2026-10-05 prototype: native `xkit start` now automatically builds/attaches a separate XalKitConsole DLL. The game has a Logs button; its owned popup renders native SDK system messages with RU tabs and filters. Two DLL messages are visible in a real captured game; game exit0 verified. Commands, module lifecycle and external CLI mirroring remain unfinished. Resource-mode automatic attachment and prebuilt distribution are not implemented. This paragraph supersedes the earlier 'no console DLL' plan statement below only for this bounded prototype.

RU/EN, 2026-10-05: pinned Dear ImGui, ImTerm and TextEditor passed a separate MSVC x86 compatibility fixture. A caller-side key API adapter and missing standard includes were sufficient; third-party sources remain unchanged. Command execution/completion, UTF8 editing/Cyrillic glyph and CPU draw-data passed. Actual Universe rendering/input, DLL installation and automatic SDK connection remain unverified.

RU: подключение девкита автоматически загружает консоль и показывает кнопку в игре. Дополнительной активации или подтверждения от пользователя нет; проверка подключения SDK выполняется внутри компонента. Отключение девкита отключает консоль.

EN: Connecting devkit automatically loads its console and exposes the in-game button. This is the default; no opt-in flag or user confirmation is required. SDK host checks are internal lifecycle validation.

RU: отдельный DLL-компонент девкита с кнопкой в игре и вкладками «Логи», «Команды», «Модули». Системные сообщения, уровни и фильтры; ввод команд, история, подсказки, список команд и подсветка. Внешняя CLI и панель используют один backend и один реестр команд; введённые снаружи команды и результаты видны в панели. Инициализация требует активного подтверждённого SDK-сеанса; обычный player-пакет её не включает.

EN: Candidate UI foundation: [Dear ImGui](https://github.com/ocornut/imgui), with its official Win32/DX9 backend and console demo. [ImTerm](https://github.com/Organic-Code/ImTerm) offers terminal completion/history; [ImGuiColorTextEdit](https://github.com/BalazsJako/ImGuiColorTextEdit) offers syntax editing. These libraries require pinned-version and x86/Universe compatibility checks. No SDK console DLL, rendering hook or UI is implemented yet. Merely loading the DLL must not create UI, hooks or an active command interface without the SDK session handshake.

RU: `native/diagnostic_bus.hpp` предоставляет `heroes5_sdk::Trace(level, module, message)` для независимых DLL. Записи содержат имя модуля, уровень, время и последовательность. Поддержаны Debug, Info, Warning, Error; текст — UTF-8, имя до63байт, сообщение до511байт. Первый callback нового starter-плагина публикует его имя. Это пока буфер сообщений: автоматический список состояний модулей и вывод в игровую консоль ещё не подключены.

EN: Use the shared bus outside DllMain and descriptor queries. The mapping stores POD only and remains alive while at least one bus holder exists; a resident SDK holder is required to retain history across full payload unloading. Writes never wait for the publication mutex; rejected writes return false, and contention increments a dropped counter. Initialization may wait up to one second. The256-record ring overwrites old records; sequence bounds expose gaps. Reads return up to64 matching records after a cursor. Invalid UTF-8/levels/oversized strings are rejected rather than truncated. Mutex abandonment invalidates storage. A two-DLL native fixture proves sharing, replacement/unload survival, bounds and filtering; it does not prove game console output.

RU: команды `setup`, `doctor`, `language`, `new`, `build`, `release`, `start`, `check` и `game` записывают начало, результат и полное время операции. Вложенная сборка связана с выпуском через `parent_operation_id`; этапы одной операции сохраняют `operation_id` и сеанс. Прогресс идёт в stderr, ответы игровых команд остаются JSON в stdout. `diagnostics` только читает существующие материалы.

EN: CLI operations record start, outcome and total duration, including session cleanup. Nested release/build operations retain their parent relationship; in-process parallel contexts remain separate. The managed watcher receives the CLI session ID. Direct invocation of older backend scripts does not yet establish the same complete CLI policy.

RU: `events.jsonl` автоматически ротируется примерно после 10 МиБ. Сохраняются пять gzip-архивов; более старые архивы событий удаляются. `xkit diagnostics` показывает текущий журнал и архивы. Очистки по возрасту пока нет. Отчёты проверок, дампы, отдельный вывод компилятора и журналы монитора этой политикой не удаляются.

EN: Event journals use [concurrent-log-handler](https://github.com/Preston-Landers/concurrent-log-handler) with a 10 MiB rotation threshold and five compressed backups. Every process uses the same settings and its own handler. This addresses the [standard FileHandler limitation](https://docs.python.org/3.10/howto/logging-cookbook.html#logging-to-a-single-file-from-multiple-processes). Retention applies only to event JSONL; raw diagnostics, monitor logs, reports and dumps remain retained. No age-based pruning is configured.

RU/EN, 2026-10-05: Python102 PASS includes four real independent writer processes producing100 complete64KiB JSON records, nested context/timing, original failure preservation during emit/close errors, gzip rotation/pruning and archive discovery. Installed CLI H5U build1.32s and native release10.34s verified operation records without launching the game. This is logging acceptance, not a new live HMR control.

RU: watcher и supervisor используют [structlog](https://www.structlog.org/en/stable/standard-library.html) поверх стандартного Python logging. Установи зависимости из requirements.txt как в README. Для человека добавь `--log-format console`: прогресс и ошибки выводятся кратко, служебные ответы скрыты. По умолчанию `--log-format json` сохраняет машинный JSONL-протокол; stdin control и проверочные стенды используют этот режим.

EN: Every watcher/supervisor writes `<output>/logs/events.jsonl`. Events carry schema_version, UTC timestamp, session_id, event_id, operation_id, process_id, plugin/instance where applicable, stage and level. All workers in one supervisor share a session; forwarding preserves originating event IDs, timestamps and process IDs. Standard logging handlers serialize writes; supervisor aggregates worker events instead of concurrent processes appending to one file. structlog provides JSON/console rendering and exception processors. Project code only supplies SDK context, status text and compiler locations.

RU: ошибки MSVC получают `diagnostics[]` с file/line/column/severity/code/message. Полный вывод хранится отдельно, путь указан в `diagnostic_log`. Консоль печатает координаты `file:line:column: error: code: message`; этот формат предназначен для IDE problem matchers, но настройка конкретной IDE пока не проверялась. У VS Code есть [готовые problem matchers](https://code.visualstudio.com/docs/debugtest/tasks). Не вставляй скрытые данные окружения в журнал.

EN: Logging acceptance sdk-log-check-20261004T203629/report.json passed with two real native local workers, parsed compiler failure in alpha and successful beta command25. Three logging regressions cover100 concurrent records, readable/noise-filtered compiler output and preserved forwarding context; all Python59 PASS. No game run was needed for this logging change. Mod-dev/game-control/native/release orchestration and IDE task integration still need the same policy; watcher adoption does not establish whole-SDK completion.

## Automatic SDK core updates / Автоматическое обновление ядра SDK

RU: добавь `--core-source native` к одиночному watcher или supervisor `--plugins`. Сохраняй изменения в исходниках ядра и CMake: стенд соберёт `heroes5_plugin_bridge` и `plugin_bridge_client`, затем заменит ядро в работающей игре. Новые единицы C++ подключай к CMake target ядра через `target_sources`; удаляя файл, убери его и из target. Первая полная сборка должна пройти до запуска тестовой игры; живой контроль проверяет готовность карты перед подключением SDK. Эта низкоуровневая команда пока не заменяет запланированный человеческий вход.

EN: `--core-source native` watches C++/headers/CMake below the selected directory, rebuilds the canonical bridge/controller targets and applies generation copies. Add/remove compilation units through the bridge CMake target. Multi-plugin updates are acknowledged sequentially; a confirmed rejection rolls already-updated plugins back before `core_update_rejected`. New-plugin discovery waits for the transaction. Compile errors keep working cores. A lost worker/unconfirmed rollback stops the manager rather than claiming a successful update. Ordinary ABI/state compatibility checks still apply.

Acceptance: `python scripts/plugin-check.py --live --multi --auto-core --toolchain <vcvarsall.bat>`. Final report sdk-acceptance-20261004T202146 PASS: new core helper77→88, new export in both plugins, source/header deletion, compiler-error isolation, second-plugin Restore rejection and first-plugin rollback, retained counters10/20, restored targeted events/UI, then full plugin files/errors/hook conflict/remove/readd and same-source player release checks. Current source hashes match. Python56/native4 PASS; full88.137s, live core builds3.18–3.21s. DevelopmentPID48932/releasePID51596 close normally, sandbox restored.

RU/EN: The acceptance CMake fixture references canonical SDK sources; it is not another SDK checkout. Core release packaging with extra CMake units, shared Game API header watching, arbitrary external dependencies and the human frontend still need work. This control removes the added core unit before player packaging; it does not prove delivery of that extra unit in the release. Overall goal stays active.

## Core replacement — overall goal still open / Замена ядра — общий goal открыт

RU: watcher теперь принимает внутреннюю команду `core-reload` с путём `bridge` к собранной DLL ядра. Кандидат загружается отдельно; старое ядро отключает callbacks, передаёт состояние, затем новое регистрирует callbacks. При подтверждённом отказе восстановления состояния старое ядро возвращается в работу. Нативные контроллеры копируются в отдельные файлы, поэтому работающий EXE не блокирует пересборку канонического клиента.

EN: Explicit core replacement is live-verified by `python scripts/plugin-check.py --live --core --toolchain <vcvarsall.bat>`. Report sdk-acceptance-20261004T200242: core1→2 adds export42 and preserves counter7; rejected restore restores the old core. The same scenario transfers the active engine CALL observer back2→1 and tests rollback with that hook installed. Successful replacements0.265/0.267s, full48.465s; developmentPID43616 and releasePID41872 close normally, deployment restored. Python53 PASS and native local handoff/rollback PASS. Source hashes match this control.

RU/EN: This is explicit replacement of built cores using the current compatible ABI/state transport. Automatic SDK-source rebuild/replacement, coordinated multi-plugin core updates, transport/ABI evolution and the human frontend remain unfinished. New source files in plugins are already watched; this result does not establish automatic discovery of new SDK compilation units. The overall SDK goal stays active. Earlier foundation-only results below are historical.

RU: пользователь уточнил приоритет: расширять весь SDK на лету, не только ускорять сборку плагина. Native PluginSnapshot переносит POD state между экземплярами ядра через version/schema/size/generation; Suspend отключает callbacks перед snapshot, Restore разрешён до регистрации hooks. Неверная схема/transport version отклоняется без изменения состояния. Это стабильный транспорт, а не копирование C++ объектов, code pointers или handles.

EN: Native4/4 tests pass. A local two-client experiment loads core1, sets counter73, suspends/exports, loads distinct core2 with added export42, restores73 and advances generation2. Artifact sdk-core-state-native.json; local processes only, no live game or automatic supervisor core swap. Rolling replacement/rollback and live ABI expansion still need implementation. Compiler setup optimization is secondary to the owner's core-HMR priority.

RU/EN: code standards and human-usable functionality/documentation rules now apply through all repository AGENTS/review profiles. Present internal command chains are not a finished human SDK frontend. README/help must explain purpose, steps, expected outcome and recovery without requiring AI.

## Several plugins in one game / Несколько плагинов в одной игре

RU: контроль общей настройки MSVC — multi194537 и одиночный194718, оба PASS; Python53/native4 PASS. Второй плагин подключён за3.183s против прежних5.213s: один замер, примерно39% быстрее. Полный multi-прогон57.588s против55.939s, поэтому ускорение всей серии не подтверждено. Тестовые игры закрыты штатно, sandbox восстановлен. Сравнение: `.local/test-state/sdk-compiler-sharing-comparison.json` в мастерской.

EN: Shared compiler setup passed multi194537 and standalone194718 live acceptance, Python53/native4. Second-plugin addition5.213→3.183s is one before/after sample (about39% reduction); whole multi55.939→57.588s did not improve. Games closed normally and sandbox deployment was restored. These results cover compiler sharing, not automatic SDK-core HMR or a finished human frontend.

Latest / Последний контроль: sdk-acceptance-20261004T182321/report.json, current sources verified, devPID24124/releasePID49900 both normal close; full55.939s, new second plugin5.213s, removal0.641s. Shared Game API headers participate in cache invalidation and include paths; watcher links bcrypt so payloads can call shared hash/build helpers. Separate hot-built h5::Sha256 consumer returned64 successfully. This supersedes175242 only for the latest source revision; older records remain scoped to their hashes.

RU: запусти слежение за общей папкой один раз. Каждая непосредственная подпапка с .cpp — отдельный плагин; имена используют строчные латинские буквы, цифры и одиночные дефисы. Добавление папки подключает новый плагин, удаление папки или последнего .cpp отключает его. Повторное добавление создаёт чистое состояние. Добавление/удаление .cpp/.h/.hpp/.inl внутри плагина также отслеживается.

```powershell
python scripts/plugin-watch.py --plugins <plugin-folders> --output <build-outside-plugin-folders> --toolchain <vcvarsall.bat> --client .local/native-check/Release/plugin_bridge_client.exe --bridge .local/native-check/Release/heroes5_plugin_bridge.dll --owned-game --main-thread --control-stdin
```

EN: Each plugin gets a distinct compiled bridge image and worker, sharing one SDK source checkout. State, generation history, compile errors and addressed commands are separate. The supervisor pins the original owned PID/creation snapshot. JSONL control adds a plugin ID, for example `{"plugin":"alpha","id":1,"command":"main","function":1,"argument":0}`. For `event`, function is the native client's first numeric argument (event value). Without a plugin ID, `{"command":"stop"}` stops the supervisor and all children. Unexpected worker failure is reported and not automatically restarted against uncertain hooks.

RU: SDK-события адресуются конкретному экземпляру; внешний message с lParam=0 остаётся broadcast. UI получает отдельные позиции. Общий WS_CLIPCHILDREN имеет счётчик пользователей на окне: отключение одного элемента не снимает защиту остальных, последний восстанавливает исходный флаг. Конфликт за одну CALL-site отклоняется, уже работающий hook сохраняется. Ошибка компиляции одного плагина не останавливает другой.

RU: при запуске общего слежения MSVC настраивается один раз. Новые и повторно добавленные плагины получают готовое окружение x86; перезапусти слежение после изменения установленного компилятора или Windows SDK. Одиночный watcher и сборка выпуска настраивают MSVC самостоятельно. `compiler_setup_seconds` измеряет настройку, а `compiler_ready.reused` показывает повторное использование. Значения переменных окружения не записываются в журнал.

EN: The supervisor prepares MSVC once per session and passes its x86 environment to new and re-added workers. Restart it after changing the installed compiler or Windows SDK. Standalone watch/release prepares its own environment. `compiler_setup_seconds` measures setup; `compiler_ready.reused` identifies reuse. Logs do not contain environment values.

EN: Up to64 active diagnostic slots; two simultaneous plugins are live-tested. Native faults still share the game process. A removed plugin's hooks/UI/state are disposed, but its stable bridge image remains resident until game exit. Arbitrary external libraries/build-system changes are not discovered automatically. Changes to an already-loaded SDK bridge/ABI require coordinated runtime replacement; this does not promise arbitrary core HMR.

Repeat acceptance: `python scripts/plugin-check.py --live --multi --toolchain <vcvarsall.bat>`. Current result sdk-acceptance-20261004T175242/report.json: developmentPID23568/releasePID50924, two normal closes and restored sandbox. It verifies source/header addition/removal, independent states10/20→22, targeted UI events, isolated compile failure, rejected conflicting engine hook, removal/restoration, fresh re-add, and two same-source release packages. Screenshot capture now checks diagnostic control pixels; plain text readback is insufficient. Earlier172417 readback passed but images lacked controls;173124 clip correction visually passed,175242 adds the pixel gate and shared game-api integration.

## Shared game bindings / Общая библиотека игры

[heroes5-game-api](https://github.com/Xaaalera/heroes5-game-api) owns supported-build SHA checks, known hook signatures/resume addresses and owned-process guards. SDK player_launch.hpp keeps compatibility aliases; plugin_bridge_client uses the shared process guard. Both existing mods consume the catalog; the predictor algorithm is not moved or resumed. Workshop: one physical game-api checkout and three consumer junctions. Standalone clones initialize pinned submodules; SDK watch includes and observes game-api headers automatically.

RU: библиотека прошла пять независимых проверок и отдельный аудит документации; замечания исправлены. Конфигурация, контракты, Git hook и CI входят в репозиторий. Инструменты проверки нужны разработчику библиотеки, не игроку и не обычному автору плагина. Требования быстрых стартов описывают именно выполняемый сценарий, без внутреннего термина review gate.

## ABI3 prototype — verified workflow / проверенный цикл

RU: для разработки использовать MSVC x86, Python3.10+ и собранные native targets. Один canonical SDK обслуживает все проекты. Начальный plugin source можно взять из native/plugin_runtime_fixture.cpp как **тестовый пример**: Query возвращает PluginApi, команда0 проверяет готовность; команды1/2 и callbacks демонстрируют состояние/новые функции. Команда6 в fixture предназначена только для native concurrency test и не входит в пользовательский API.

EN: Put your C++ sources in a separate directory, include plugin_runtime.hpp from this SDK, export Heroes5PluginQuery, and implement command0 as a side-effect-free readiness probe. State is a bounded POD buffer owned by the bridge; preserve schema/size across reloads. The SDK must own callback addresses. No payload-owned threads, retained state-buffer pointers or direct hooks. Supported engine hooks are verified E8 CALL observers on the window thread; they preserve original execution and may skip observation while the bridge is busy. Native crashes and irreversible game effects are outside rollback.

Build the SDK once / Собрать SDK один раз:

```powershell
cmake -S native -B .local/native-check -A Win32
cmake --build .local/native-check --config Release
ctest --test-dir .local/native-check -C Release --output-on-failure
```

Watch a plugin after launching the owned sandbox / Следить за плагином после запуска своей песочницы:

```powershell
python scripts/plugin-watch.py --source <sources> --output <build-outside-sources> --toolchain <vcvarsall.bat> --client .local/native-check/Release/plugin_bridge_client.exe --bridge .local/native-check/Release/heroes5_plugin_bridge.dll --owned-game --main-thread
```

RU: --control-stdin добавляет JSONL команды разработчика. Например `{"id":1,"command":"main","function":1,"argument":0}`; ответ status=command_result содержит числовой bridge status. `{"command":"stop"}` штатно отключает callbacks. GUI-приложение/WebSocket для этого протокола не требуются и пока не реализованы. Генерации лежат в output; source/header/SDK-header изменения отслеживаются, неизменившиеся object files берутся из кеша. Сборка пользовательских внешних библиотек пока вне этого интерфейса.

EN: --release NAME --loader PATH packages the same sources plus release bridge into a player ZIP (see release section below). Players use normal game startup, without Python/compiler/client. H5U is for supported resource/script contexts; native C++ still requires DLL/bootstrap.

Repeat the full live acceptance / Повторить полную живую проверку:

```powershell
python scripts/plugin-check.py --live --toolchain <vcvarsall.bat>
```

RU: задать H5_WORKSPACE с подготовленной собственной .local/test-game и использовать venv с keystone/unicorn. Команда отказывается запускать вторую игру, проверяет владение PID/creation, проводит source edits, callbacks/UI/state/errors, точное восстановление CALL bytes, собирает ZIP и запускает его обычным путём. Перед закрытием подтверждается загруженная WorkshopPolygon, затем оба процесса закрываются штатно и release deployment откатывается. JSON/PNG и тайминги сохраняются в .local/test-state/sdk-acceptance-*/. Это явный live test, не часть unit suite.

Final acceptance / Итоговая приёмка: sdk-acceptance-20261004T170427/report.json + audit.json; development PID49896, release PID36064, both normal close. Source→UI2.1012827s, whole38.385497s. Source hashes, every applied DLL hash and package hashes match; release uses exactly the successful live payload source. Source bridge SHA f248590a8f21d1671586d6a0dbe46d4706ba6b5be1207929e75c6a7ac77f4301. Python51/51 and CTest4/4 PASS, no skips. Machine test verifies GPR/ESP/flags including DF, stack argument and seeded x87/SSE against direct original call; C++ callback executes with DF cleared, original flags restored. First test seeded only CF and gave an invalid flags comparison; fixed test seeds all relevant flags. No production DLL fix was inferred from that initial test failure.

Prototype acceptance covers the requested development/release cycle. General UI widgets, arbitrary prologue/multithread hooks, state schema migration and native crash containment are future extensions, not implied by these results. Existing predictor/reference DLLs retain legacy contracts and require an explicit migration before using this ABI. Work remains uncommitted/unpublished.

## 2026-10-04 — ABI3 engine CALL hook and concurrent reload

RU: текущий ABI3 добавляет engineCallSite/engineOriginalTarget/engineCommand. Поддержан один проверяемый x86 E8 CALL на потоке игрового окна; установка/снятие происходят там же, исходные5bytes и executable protection проверяются. Постоянный thunk сохраняет GPR/flags/FPU/SSE и делает tail-jump в исходную функцию. Это не перенос произвольного пролога, не multi-thread detour и не замена сигнатуры функции. Payload не хранит raw pointers старого поколения. При занятом bridge lock диагностический engine callback пропускается, оригинальная функция всегда вызывается.

EN: Reload now refreshes subscriptions on the main thread before acknowledgment and restores the previous payload/settings if registration fails. Native concurrency test keeps an old call blocked while another thread requests reload; reload waits, old call returns its original result, then new code runs. CTest3/3 PASS including this boundary. Dedicated machine-state preservation test remains to add; don't infer full register coverage from game survival.

Live PID52212: the ScriptDispatchCall observer changes handler3→4 and preserves counter progression; an invalid neighbouring hook location is rejected and the previous handler restored. Disable/Stop restores the original script dispatcher and stops observer updates. Numeric bindings and byte-level evidence remain in sdk-engine-hook-first-live.json. Functional checks pass; whole run remains failed because OCR close timed out. Visually confirmed existing OK dialog was clicked without focus/cursor changes; WaitForExit then confirmed normal exit.

RU: OCR пропускал маленькую кнопку OK; retained image после2x scaling распознаёт prompt/cancel/OK. Dialog OCR теперь увеличивает crop и переводит координаты обратно. Отдельный слишком ранний close-контроль PID46704 встретил смену startup window и затем заставку; normal recovery failed, owned PID/creation verified before forced termination. sdk-exit-ocr-live.json. Это не успешный gate закрытия; следующий live контроль должен сначала подтвердить готовую карту. Не повторять запуск только из-за тайм-аута наблюдения.

## 2026-10-04 — current prototype and release notes / текущий прототип

RU: выбран MSVC x86 с автоматической инкрементальной сборкой. Watcher кеширует object files по исходнику, headers, SDK headers и настройкам компилятора; изменившийся .cpp собирается отдельно, правка header консервативно пересобирает все units. Затем новая DLL применяется в текущей игре. C++ JIT остаётся неподтверждённым вариантом; готовый путь сохраняет тот же C++ исходник и ABI для разработки и выпуска.

EN: current prototype supports one active payload per developer bridge, C ABI2, up to4096 bytes of host-owned POD state, command dispatch and a custom window-message event subscription with numeric diagnostic UI. Payloads must use bridge-owned callbacks; direct engine detours are not implemented by this API. Schema changes reject; explicit migration/reset, arbitrary build systems/external include dependencies and a general UI toolkit remain open. Native crashes/external game effects cannot be rolled back.

Live evidence / Живые проверки:

- PID52020: event registration, UI2007→2010, state retention, unsubscription/UI removal and normal close. Capture sdk-event-ui-first-live.png visually confirms numeric UI over the actual adventure map. This is a Windows child control, not a game XDB widget.
- PID49128: automatic edits update callback/UI2001→4002; rejected new probe rolls back to working code, compiler failure keeps it; next events4003/4004 confirm retained state. Save→UI2.162s, full15.217s, normal close. sdk-auto-event-ui-live.json.
- PID19292: packaged DLL starts through ordinary dinput8 bootstrap without watcher/client; UI0→2001 proves startup/event. Full6.689s, normal close, sandbox loader restored and sample plugin removed. sdk-release-first-live.json.
- Real two-source compilation: compiled units2→1→0→2→1 (initial, one .cpp edit, cache hit, header edit, release bridge only); respective builds1.799/0.153/0.102/1.766/1.835s. Not general performance bounds. sdk-incremental-build.json. SDK-header watcher metadata was added afterward.

### Build a release / Собрать выпуск

```powershell
python scripts/plugin-watch.py --source <plugin-source-directory> --output <outside-source-build-directory> --toolchain <Visual-Studio-vcvarsall.bat> --release my-plugin --loader .local/native-check/Release/dinput8.dll
```

RU: получается my-plugin.zip: bin/Heroes5Mods/Plugins/my-plugin.dll, общий bin/dinput8.dll и release.json с хешами. DLL включает тот же payload и постоянный bridge; Python/компилятор/отдельный EXE игроку не нужны. Bootstrap автоматически вызывает Heroes5PluginInstall у DLL в выделенной папке Plugins; существующие два legacy-плагина загружаются прежним путём. Install=1 означает принятую асинхронную инициализацию; Heroes5PluginReleaseStatus=2 подтверждает завершение. Не публиковалось.

EN: Install the package's bin tree into the compatible game, using one shared SDK dinput8 bootstrap. Removal of a plugin removes its DLL; retain the shared bootstrap while other mods depend on it. Release source uses the same cached payload objects; only the bridge adds release startup code. The package does not contain game files or developer EXEs.

### H5U boundary / Граница H5U

RU: mod-dev.py уже собирает ресурсные H5U. H5U подходит для ресурсов и скриптов, которые действительно подключает игровой контекст. Универсальная загрузка произвольного Lua из архива не доказана. Нативные функции этого SDK требуют DLL/bootstrap; переименование ZIP в H5U или помещение DLL внутрь не добавляет её загрузку. Возможен мод с DLL и дополнительным H5U; самостоятельный native-only H5U не реализован.

EN: H5U resource/script delivery depends on engine resource references and script context. Archive packaging alone does not execute arbitrary scripts or DLLs. The native release path is DLL plus shared bootstrap, optionally accompanied by an H5U resource package.

Current checks: Python51/51 and native CTest3/3 PASS, no skips. Synthetic tests do not establish arbitrary engine-hook or game-widget support. Bad candidate probes now restore and re-observe the previous payload. Busy custom events are re-posted rather than blocked on the bridge lock; overload/delivery failure stress remains open.

## 2026-10-04 — ABI2 event/UI work in progress

RU: автоматический watcher --main-thread прошёл живой контроль PID48800:1→2→compiler error→1, save→call1.977s, полный цикл10.359s, normal game_closed. Артефакт sdk-auto-main-first-live.json. Этот контроль был до следующего ABI2 изменения и не доказывает UI/events.

EN: current ABI2 descriptor adds eventMessage/eventCommand/showEventResult. Fixturev2 registers a custom WM_APP event and command5; bridge owns a WH_GETMESSAGE subscription and numeric child STATIC output, refreshes on main calls and removes on Stop. Client `event <argument> 0` posts the event. Build/CTest3/3 PASS; live event delivery, rendered child output and removal are unverified. Busy bridge lock may skip queued events; arbitrary engine detours are not implemented. Do not present this as finished UI/HMR support.

## 2026-10-04 — window-thread dispatch / вызовы на потоке окна

RU: bridge добавляет Heroes5PluginInvokeMain, клиент — `main <command> <argument>`, watcher — --main-thread. Постоянный host-owned поток устанавливает WH_CALLWNDPROC только на поток видимого окна собственного процесса; зарегистрированное сообщение синхронно вызывает текущий payload. Повторные вызовы сериализованы с Replace/Stop. Этот контроль подтверждает thread affinity, не разрешает произвольные engine calls в любой фазе и не создаёт gameplay UI.

EN: First live attempt PID52168 loses the hook after its installing remote thread exits: first main call passes, next returns102; normal close, failure retained. A persistent hook-owner thread fixes the observed lifetime. Retest PID48772 passes48 commands: independently checked window-thread ID, new command42, counter7 retained through10 DLL switches and5 schema rejections; normal client Stop/unhook and game_closed. Whole cycle6.125s. Artifacts sdk-main-thread-first-live-failure.json and sdk-main-thread-first-live.json. No automatic source build in this second control; watcher main-thread option was added afterward and awaits live save→main-call verification.

Lifetime / Время жизни: after main dispatch the bridge image stays resident until game exit, even after unhook; payload/runtime and installer thread are explicitly stopped. A hung window/payload makes the remote client time out and retain code/request memory; native crash recovery absent. Main-thread callbacks must not re-enter this bridge. Initial hook failure does not trigger game relaunch.

Sources: [thread-specific Windows hooks](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowshookexw), [hook execution thread](https://devblogs.microsoft.com/oldnewthing/20180926-00/?p=99825). Next: automatic source updates observed on this thread, actual UI/events and validated engine-hook registration/removal, release packaging.

## 2026-10-04 — automatic native watch prototype / автоматический цикл

RU: scripts/plugin-watch.py следит за .cpp/.h/.hpp/.inl в исходной папке, с debounce0.1s автоматически собирает x86 DLL в уникальной папке поколения и применяет через постоянный native/plugin_bridge.cpp. MSVC environment готовится один раз. Изменение исходников во время сборки отклоняет устаревшую версию; compiler/ABI/schema error сохраняет предыдущую. Произвольные build systems/dependencies пока не поддержаны; нужны self-contained C++ sources и plugin_runtime.hpp ABI. SDK headers вне source folder пока не отслеживаются.

EN: Developer-only bridge client supports local checks and an explicitly owned sandbox process with creation/path/hash validation. Calls use remote worker threads, not a game API/main-thread boundary. Probe command0 convention reports code version. Game hooks/UI, concurrent game callbacks, schema migration/reset, release packaging and production bootstrap integration remain open. Python is a development tool, not a player dependency. Stop normally with Ctrl+C; --max-updates bounds research runs. Force-killing the watcher is not confirmed cleanup.

Build / Сборка: `cmake -S native -B .local/native-check -A Win32`, then `cmake --build .local/native-check --config Release`; use installed Visual Studio CMake if it is not on PATH. From the devkit directory:

```powershell
python scripts/plugin-watch.py --source <plugin-source-directory> --output <outside-source-build-directory> --client .local/native-check/Release/plugin_bridge_client.exe --bridge .local/native-check/Release/heroes5_plugin_bridge.dll --toolchain <Visual-Studio-vcvarsall.bat>
```

RU: добавить --owned-game только после обычного SDK запуска собственной песочницы с --control и корректным H5_WORKSPACE; клиент читает native-probe.json, stale PID отклоняется. Для игрока отдельный EXE не вводится. Payload command0/1/2 в fixture — версия/счётчик/новая функция; они не являются игровыми API.

EN: Real local compiler/bridge integration passed changed/new functions, state retention, compiler-error recovery and schema rejection. Automatic polling CLI passed local save→call1.940s. Owned visible game PID3320 passed automatic v1→v2→syntax error→v1 in the same process, save→observed call2.126s; whole launch/check/normal quit9.286s. Generation reaches3; close reports game_closed. One sample; no live state/hook/UI acceptance. Artifacts sdk-watch-local-integration.json, sdk-auto-watch-local.json, sdk-auto-watch-first-live.json. First live attempt used Python without keystone, failed before launch completion and left no game; dependency-checked venv retry passed. Added payload hashing after this live sample; the live artifact predates that metadata addition.

Tests / Проверки: watcher failure/race/path/header boundaries4/4 PASS, native CTest3/3 PASS, no skips. No claim of native crash recovery or rollback of external game effects.

## 2026-10-04 — native lifecycle prototype / прототип жизненного цикла

RU: native/plugin_runtime.hpp вводит C ABI Heroes5PluginQuery и host-owned POD state до4096bytes. Replace проверяет ABI/schema/size и переключает DLL под общей блокировкой с Invoke; старый модуль выгружается после завершения вызовов. Invoke работает с копией состояния, commit только при успешном статусе. Нет cached payload pointers у вызывающего. DLL пути должны указывать на готовые файлы поколений; автоматический watcher ещё не реализован.

EN: Trusted authored payloads only. Query/DllMain are side-effect-free; no payload-owned threads, retained state pointers or direct game hooks. Calls are serialized and must not re-enter the runtime. Rejection restores POD state/result, not external game effects or native crashes. State schema changes currently reject reload; migration/reset is not implemented. This host is not connected to the game's bootstrap yet.

Verification / Проверка: MSVC x86 Release build exit0; CTest3/3 PASS (existing launch boundaries, DirectInput forwarding, new plugin runtime lifecycle), no skips. Lifecycle exercises changed/new functions, state retention across20 reloads, ABI/schema rejection and rejected-call rollback. Not live game acceptance; hooks/UI/events/watch/package remain open.

## 2026-10-04 — universal plugin SDK / универсальный SDK плагинов

RU: новый SDK goal active по запросу владельца; предиктор на паузе. Исследование кешированного окружения MSVC: setup1.452s, пять сборок маленькой stateless x86 DLL0.565/0.375/0.386/0.380/0.388s, все exit0. Игру не запускали; hooks/state/reload не проверялись. Артефакт sdk-hmr-warm-build-timing.json. Постоянный watch-процесс может переиспользовать окружение; общий save→game результат пока не измерен. Первая попытка замера завершилась ошибкой кавычек cmd до сборки, исправленная попытка завершена.

EN: Cached MSVC environment removes repeated setup in this bounded experiment. Five tiny payload builds succeeded, not a full-mod or in-game latency benchmark. Persistent host/lifecycle and automatic watch/apply remain to implement. Embedded C++ candidates still need Windows x86/ABI validation; no suitable interpreter was found on the current PATH. [Clang-Repl's documented JIT pipeline](https://clang.llvm.org/docs/ClangRepl.html) still compiles source internally. [Live++ documentation](https://liveplusplus.tech/docs/documentation.html) requires license activation; do not assume it can be redistributed as the SDK's default backend.

RU: цель владельца — разные плагины для других разработчиков: сохранить исходник → увидеть результат в той же игре → выпустить готовый мод из тех же исходников. Ручная пересборка на каждой правке исключается из рабочего цикла. Сравнить встроенное выполнение/JIT и автоматическую инкрементальную C++ компиляцию; внутреннюю компиляцию не называть её отсутствием. Не ограничивать SDK правилами расстановки.

EN: Build a reusable SDK for arbitrary plugin features. Save source, observe the result in the same game, then release the same source. Compare embedded execution/JIT with automatic incremental native compilation; avoid a separate rewrite for release.

- Acceptance / Приёмка: changed/new functions, added/changed hooks, UI/events, preserved or explicitly reset state, broken-update rollback and repeated reloads in one process. Measure save-to-observed-result.
- Packaging / Поставка: native plugins currently require the DLL bootstrap. H5U resource overrides are verified separately; putting a DLL in H5U does not make the game execute it. Research script-only H5U and optional SDK-assisted packages. Standalone native H5U delivery is not proven.
- Candidates / Кандидаты: [Cling C++ JIT](https://cling.readthedocs.io/en/latest/) and [Live++ hot reload](https://liveplusplus.tech/docs/documentation.html). Verify game x86 compatibility, ABI, dependencies, distribution terms and development/release parity before choosing. Neither integration is implemented yet.

## 2026-10-04 — native HMR research, not a released command

RU: в собственной видимой игре PID50664 исследовательская x86 DLL без hooks вернула1; при том же PID исходник изменён, DLLv2 пересобрана, после загрузки функция вернула2 и новый export42. Правка→новый вызов2.946s, сборка2.890s; полный цикл игры7.220s. Обе исследовательские DLL выгружены после завершения вызовов, игра штатно закрыта. Попытка открыть загруженную DLL для записи вернула sharing violation32, поэтому нужна копия с уникальным именем поколения.

EN: This proves changed/new stateless native exports in one live game process, not reload of existing mods or hooks. Local evidence sdk-native-hmr-first-live.json; first runner failed before PID because workspace configuration was missing. Current bootstrap is init-once. Predictor retains9 normal/15 Superadmin detours, WndProc and provider callbacks, without an uninstall export; bank selector also lacks teardown. Do not FreeLibrary existing mods while game references remain.

Next / Далее: permanent host/bridge with explicit ABI, callback quiescence, state migration and generation-named payloads. Calls must resolve through the current generation rather than cache raw payload pointers. New hook registration needs validated sites and transactional detach/attach; game-facing operations belong on a verified safe main-thread boundary. Retain PID/creation/ownership guards. WebSocket/map restart follow native reload proof; Lua-only reload is insufficient. No production HMR command implemented yet.

Sources / источники: [FreeLibrary lifetime](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-freelibrary), [DllMain and loader lock](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-best-practices). DLL loading/unloading and thread waits belong outside DllMain.

## 2026-10-04 — levelup-state / нативное окно повышения уровня

RU: `game_control.py levelup-state` пассивно читает жизненный цикл CLevelUpBox закреплённой сборки. Observer подключается к созданию и уничтожению окна до первого запуска собственного процесса; исходные инструкции проверяются. Отдельные страницы кода и данных не пересекают буферы команд/результатов. Поля available/stable/open/objects/created/destroyed/sequence; предел4 объекта. Проверяются владелец процесса, целостность hooks, подпись данных, стабильность снимка, баланс объектов и тип окна. Ошибка/неизвестность не означает закрытое окно.

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

Поправка 2 октября 2026: стенд сверяет и переставляет два import descriptors закреплённого EXE только в памяти собственного приостановленного процесса: Granny перед Universe d3d9. Защита страницы восстанавливается. Один контроль без отладчика загрузил карту с ожидаемым размещением библиотек и штатным выходом. Игровые файлы на диске сохранены; пользовательская поставка этим не исправляется.

Нативный запуск сверяет четыре игровых SHA-256 и исходные байты перед изменением собственного приостановленного процесса; EXE/DLL на диске не патчатся. Полная изоляция всех записей профиля вне тестовой папки не доказана. Не заменять `d3d9.dll`, `uni.dll` или `um.dll` сторонним загрузчиком ради этого стенда.

Полигон содержит проверки обычных нападений и отдельные StartCombat-арены. Они отвечают на разные вопросы. Генератор и общий devkit не дают обойти дневной максимум движения; такой обход был отдельным экспериментом предиктора.

## EN

October 2, 2026: the probe verifies and swaps two pinned EXE import descriptors only in its own suspended process, loading Granny before Universe d3d9 and restoring page protection. A debugger-free control loaded the map with the expected library layout, then exited normally. On-disk game files are preserved; this does not repair player delivery.

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

RU: прямой supervisor прекращает работу при ошибке worker или тайм-ауте его отключения. Пока очистка не подтверждена, слот не используется заново и новый экземпляр не подключается. Перед следующим запуском закрой собственную тестовую игру.

EN: The direct supervisor stops on worker failure or retirement timeout. Unconfirmed cleanup prevents slot reuse and admission of a new instance. Close the owned test game before restarting.

RU: `game_control.main(argv)` принимает список CLI-аргументов и возвращает тот же структурированный результат без stdout. Импорт native-probe переиспользуется в этом процессе; каждый вызов по-прежнему проверяет текущий PID/creation time/patches. Не хранить открытый game handle между загрузками. `main()` без argv сохраняет обычный CLI JSON stdout и существующий serve. EN: Caller must set its workspace before importing the SDK. Reuse changes client startup overhead, not game readiness; all ownership/signature/readback guards remain. Full SDK46/46 and owned three-battle consumer control PASS; ordinary game DLL behavior unchanged.
## Команды: сохранение вывода / Commands: retained output

RU, 2026-10-05: ручная замена DLL консоли в работающей игре прошла с сохранением текста, истории и результатов. Несовместимая версия состояния отклонена, прежняя консоль восстановлена. Старые команды не выполнялись повторно. Автоматическая реакция на изменение исходников, перенос выбранной вкладки и положения окна ещё требуют завершения.

EN (earlier manual proof): Console DLL replacement preserved editor text, history and results, with incompatible-state rollback and no replay. Current source-watch and checkpoint behavior is described below; this earlier run did not verify selected tabs or window placement.

RU: Консоль сохраняет редактор, историю, вывод и ожидающие результаты в памяти сеанса SDK. При восстановлении старые команды не выполняются заново. Watcher поддерживает сборку и замену консоли; физический ввод и геометрия окна требуют отдельной живой проверки.

EN: Console editor, history, output and pending-result IDs checkpoint in SDK session memory without replay. Source watch supports console build/replacement; physical input and window geometry remain separate live checks.

RU: При замене DLL SDK останавливает старую консоль и передаёт её состояние новой. Несовместимое состояние отклоняется с возвратом прежней версии. Ранние записи об отсутствии checkpoint и необходимости перезапуска относятся к предыдущим этапам разработки.

EN: DLL replacement stops the old console and transfers its state. Incompatible state is rejected and the previous version restored. Earlier missing-checkpoint/restart notes describe prior development stages.

RU/EN, 2026-10-05: current-source native acceptance PASS (`--live --multi --auto-core`): new core exports/functions/hooks in the same process, retained state, coordinated rollback and independent plugin lifecycle, followed by two separate same-source DLL player packages. Report sdk-acceptance-20261005T171156. Both games closed normally and sandbox restored. This does not accept console component HMR or H5U lifecycle.

RU, 2026-10-05, исправление: `level Brem 20` и `level Calid 20` прошли в одном работающем процессе. Перед каждым выбором SDK ждёт новый диалог; объект скрытого предыдущего окна может ещё существовать. Недоступный номер предложения заменяется первым действительным, при отсутствии предложений выбор навыка пропускается. Ошибка чтения не считается отсутствием вариантов. Монитор теперь сохраняет полный дамп, пригодный для анализа состояния окна; файлы остаются локально.

EN: Both level Brem20 and level Calid20 (space-separated commands) completed in the same process; Calid started at level1. Each step waits for a newly created dialog rather than any retained old object. An unavailable choice falls back to the first valid offer; no offers means no skill-selection event. Read failures remain errors. ProcDump now captures full dumps for window-state diagnosis; dumps remain local.

RU: кнопки «Подсказки» и «История» вставляют выбранную команду в редактор. Для уровня указывай имя героя: `level Brem 20`; список имён даёт `heroes`. Ошибки аргументов показывают формат команды. Живой прогон повышения уровня 2026-10-05 завершился сбоем игры; проверка исправления ожидания окна ещё идёт.

EN: Suggestions and History insert the selected command into the editor. Include the hero name: `level Brem 20`; `heroes` lists names. Argument errors show command usage. The 2026-10-05 live level-up run crashed; the corrected modal-wait behavior still needs live acceptance.

RU, 2026-10-05: команда и результат остаются во вкладке «Команды» при переключении вкладок. Вывод прокручивается внутри панели; завершающий перенос строки редактора больше не мешает выполнению. В игре проверено выполнение heroes и возврат Logs→Commands. Автодополнение и выбор истории в новом встроенном редакторе ещё требуют завершения.

EN: Commands and results persist across tab switches in a scrollable embedded panel. Editor trailing newline no longer prevents execution. The owned-game check executed heroes and returned through Logs→Commands. Completion and history selection in the embedded editor remain unfinished.

## Камера и горячие клавиши не работают / Camera controls and hotkeys fail

RU: Такой сбой встречается и в базовой игре. В подтверждённых пользователями случаях помогло восстановление `input_a2.cfg` в выбранном профиле.

1. Закрой игру. Найди выбранный профиль в `Документы/My Games/Heroes of Might and Magic V — Tribes of the East/Profiles`.
2. Если в нём есть `input_a2.cfg`, сохрани резервную копию перед заменой.
3. Скопируй `profiles/default_profile/input_a2.cfg` из установленной игры в этот профиль и запусти игру снова.
4. Проверь отдельно колесико, стрелки и горячие клавиши. Восстановление стандартного файла заменяет пользовательские привязки, если они были.

EN: This also occurs in the base game. Players report recovery after restoring `input_a2.cfg` in the selected profile under Documents/My Games. Close the game, back up any existing profile input file, copy `profiles/default_profile/input_a2.cfg` from the installation into that profile, then restart. Check wheel zoom, arrows and hotkeys separately. Replacing an existing file replaces custom bindings.

Sources / Источники: [firsthand reports and recovery](https://www.reddit.com/r/HoMM/comments/np8cux/heroes_v_keyboard_issues/), [HeroesWorld report](https://heroesworld.ru/topic/14408/goryachie-klavishi-i-kamera/). A missing file is a diagnostic clue, not proof that every input failure has this cause.

## Консоль xkit / xkit console

RU: вкладка «Логи» показывает краткие сообщения SDK и понятные ответы игровых команд. Полные служебные результаты остаются в JSON-журнале рабочей папки. Если консоль не подключилась, причина выводится в терминал до закрытия сеанса; для диагностики используй `xkit diagnostics`.

EN: The Logs tab shows concise SDK messages and readable game-command results. Full service results remain in the workspace JSON journal. Failed console startup reports its cause in the terminal before session cleanup; use `xkit diagnostics` to inspect the failure.

RU: `xkit start` ресурсного H5U-проекта подключает SDK-консоль из готовой папки `runtime` девкита. Файлы и соответствие исходникам проверяются до запуска игры. Для автора H5U компилятор не нужен. Если разработчик SDK собирает новое готовое поколение командой `xkit sdk build`, текущий H5U-сеанс подхватывает ядро и консоль. При отказе новой консоли возвращается прежнее ядро. История и ответы сохраняются; в журнале появляется «SDK обновлён» или сообщение об отклонении. Изменения самого H5U по-прежнему требуют остановки, сборки и нового запуска.

EN: Resource-only `xkit start` attaches the ready SDK runtime without a compiler. A maintainer's `xkit sdk build` publishes a generation that the running session picks up automatically. Core and console transfer together; confirmed console rejection rolls the core back. History/results remain. SDK update/rejection is logged. H5U resource edits still require stop, build and start.

RU/EN: Подключение SDK без DLL мода поддерживает замену ядра и откат: новая версия сначала готовится без callbacks, затем восстанавливает проверенное пустое состояние и активируется. / Payload-free SDK core replacement prepares without callbacks, restores a validated empty state and then activates; failed restoration rolls back. Timeout or unknown teardown state ends the owned session rather than forcing another activation.

RU: В PowerShell после установки автодополнения `xkit game army Brem ` + Tab предлагает типы существ из установленных ресурсов. Префикс вводится как в константе, например `CREATURE_AR`. Tab не выполняет действие в игре. Для подсказок имён сначала вызови `xkit game heroes`; после смены состава героев повтори запрос. В игровой консоли список обновляется автоматически.

EN: PowerShell completion offers creature types after `xkit game army Brem `. Use an uppercase constant prefix such as `CREATURE_AR`. Tab dispatches no game action. Run `xkit game heroes` to populate hero suggestions, and repeat after the roster changes. In-game hero suggestions refresh automatically.


RU/EN: Наличие файла управления проверяет `xkit doctor`; это не заменяет проверку ввода в игре. / `xkit doctor` checks candidate profile binding files; it does not certify live input behavior.

### Дополнительные exports / Additional exports

RU: HMR меняет ядро плагинов и консоль, включая поддерживаемые функции и callbacks. Начальные загрузчики и графическая оболочка относятся к запуску процесса; их новая версия применяется при следующем запуске. Размещай новый игровой функционал в ядре или отдельном плагине, а не в стартовой оболочке.

EN: HMR replaces plugin cores and console modules with supported functions/callbacks. Startup bootstrap and graphics-facade changes take effect on the next process launch. Put new gameplay behavior in the core or a plugin rather than the startup shim.

RU: Добавь один файл `.def` в исходники плагина для дополнительных экспортируемых функций. Watcher замечает его добавление и изменение; выпуск использует тот же файл. Правка только `.def` повторно линкует готовые объектные файлы. Чтобы вызвать новую функцию, её потребитель должен поддерживать её ABI.

EN: Add one `.def` file to plugin sources for additional exports. Watch and release use the same definition. Export-only edits relink cached objects. Calling a new function requires an ABI-compatible consumer.

### Нагрузка и кеш сборки / Background load and build cache

RU: В фоне простаивающая тестовая игра ограничена по CPU. Команды временно снимают ограничение: до 120 секунд для карты, перезапуска и меню, до 15 секунд для остальных действий. Это предел окна активности, а не гарантия завершения команды. Для длинных папок проектов SDK использует короткие каталоги промежуточных файлов; исходники и готовые пакеты остаются в своих папках.

RU: После переноса SDK или установки новой копии архива сборщик сохраняет старый кеш CMake и выбирает отдельный для нового расположения. Проверка длины применяется к окончательному каталогу, включая кеш консоли. Переносить проект вручную ради сборки не требуется; готовые пакеты сохраняются в папке проектов.

EN: Moving SDK source or installing another archive keeps the old CMake cache and selects a separate cache for the new source location. The length check applies to the final directory, including console builds. You do not need to move the project to rebuild; release packages remain in the workspace.

EN: Idle background games have a CPU cap. Owned commands temporarily release it for up to 120 seconds for map/restart/menu and 15 seconds for other actions. These are activity limits, not completion guarantees. Long project paths use short intermediate build directories; sources and release packages stay in their original locations.

### Сохранение графической библиотеки / Graphics library recovery

RU: Сеанс SDK сохраняет оригинальную графическую DLL в своей тестовой копии и восстанавливает её после подтверждённого выхода игры. Если файлы изменены извне или выход не подтверждён, они сохраняются для восстановления; журнал сообщает об ошибке. Работа этого пути проверена вместе с ядром, консолью и HMR на текущей Windows.

EN: SDK sessions retain the original graphics DLL in their private test copy and restore it after confirmed game exit. External file changes or unconfirmed exit preserve the recovery files and report a failure. This path is verified with core, console and HMR on the current Windows build.
## Bank adapter development status / Статус адаптера справочника

### Восстановление файлов после ошибки / File recovery

Перед установкой в тестовую копию SDK сохраняет прежние DLL/H5U на диске. Если завершение или восстановление не подтвердились, журнал указывает путь `recovery.json`. После закрытия тестовой игры выполни:

```powershell
xkit sdk recover-bank <путь-к-recovery.json>
```

Команда проверяет рабочую папку, назначения и контрольные суммы; изменённые извне файлы не перезаписывает. Успешное восстановление удаляет сохранённые копии. Блокировка установки и восстановления освобождается ОС при завершении процесса; оставшийся файл блокировки сам по себе не означает активную операцию. Перенос тестовой копии в другую рабочую папку этим восстановлением не поддержан.

EN: SDK retains previous bank DLL/H5U files and recovery.json on disk before installation. If cleanup fails, close the test game and run the command above using the manifest path from the log. Recovery validates the workspace, allowed destinations and checksums, refuses external edits, and removes retained backups after success. OS leases exclude simultaneous mutation and release on process exit; the remaining lease file is not an active-lock indicator. Moving a sandbox to another workspace is outside this recovery contract.

Developer evidence: a subprocess staged files under an OS lease, a concurrent acquisition was refused, then the child exited without finally cleanup. Normal named recovery restored all originals from disk. This is process-termination verification, not sudden-power-loss durability. [Windows lock lifetime](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex) · [Python file locking](https://docs.python.org/3.13/library/msvcrt.html).

RU: установка, восстановление и очистка копий используют одну блокировку через уже подключённую библиотеку portalocker. Занятую копию очистка пропускает; ресурсы в ней не заменяются и архив не создаётся. После получения блокировки состояние игры проверяется повторно. Любой `xkit start` удерживает блокировку от подготовки до создания процесса игры, чтобы очистка не попала в этот промежуток.

EN: Staging, recovery, resource sharing and retirement use the same workspace lease through the existing portalocker dependency. Busy copies are skipped. Storage rechecks closed-game state after acquisition, before sharing or archiving. Every named start retains the lease through preparation and owned game creation.

RU: обычный `xkit start army-reference --map WorkshopPolygon` теперь запускает нативный сеанс справочника: собирает managed DLL и H5U, устанавливает их только в подготовленную тестовую копию, подключает watcher и восстанавливает прежние файлы после закрытия игры. В живом прогоне подтверждены применение DLL и штатное завершение. Также проверены завершённая автоматическая замена ядра и дополнительный export после правки исходника. Сохранение заполненной карточки склепа проверено при обновлении DLL справочника и ядра SDK. При завершении сначала останавливается watcher, затем сервис консоли, чтобы текущие операции не теряли свой host.

EN: Named bank start prepares the managed native DLL/H5U and runs the owned watcher. First application, automatic core replacement and a newly available core export are live verified in the same game process. Stop waits for the watcher before closing console host services. Actual crypt-card rendering and populated cache continuity across automatic bank payload replacement are verified. Populated crypt-card cache continuity through core replacement is also live verified.

RU: карточка склепа проверена физическим вводом и снимками. После автоматической сборки/применения обратимой правки исходника справочника сохранены одно кешированное окно и наблюдаемые счётчики; карточка снова отображается. Правка была комментарием, поэтому эта проверка подтверждает цепочку обновления и сохранение окна, а не изменение поведения функции. Заполненный кеш карточки склепа также сохранён при автоматической замене ядра; нативный снимок подтвердил повторное отображение.

RU: также проверен отказ несовместимого ядра при восстановлении состояния. После автоматического отката прежняя функция продолжила возвращать ожидаемый результат, hook справочника восстановлен. Счётчики в этом сценарии были нулевыми; сохранение заполненного кеша карточки остаётся отдельной проверкой.

EN: A core candidate that refuses state restore was rejected in the live named session; rollback restored the previous exported behavior and bank hook. The observed counters were zero, so populated cache retention remains unverified.

RU: `xkit build army-reference` и `xkit release army-reference` учитывают `native_adapter: bank-selector` в рецепте. Результат — ZIP с отдельной WorkshopBankReference.dll, соответствующим H5U, общими dinput8.dll/d3d9.dll, уведомлениями о лицензиях и инструкцией установки. Для выпуска используется обычный контракт запуска справочника, не вариант, требующий активного контроллера разработки. Самостоятельный checkout подключается как проект мастерской согласно разделу настройки. Нативный сеанс разработки запускается через xkit start; границы его проверки указаны выше.

EN: Bank build/release honors the recipe's bank-selector native adapter and packages the player DLL, matching H5U, shared input/graphics infrastructure, licenses and installation instructions. Player compilation uses ordinary startup, not the controller-dependent managed development variant. Named native start is available with the verification limits above.

RU: `build.json` внутри ZIP содержит контрольные суммы файлов и относительные имена исходников. Перед упаковкой DLL, H5U и графическая библиотека повторно сверяются с результатом нативной сборки. Изменённый H5U не выпускается со старой DLL; прежний ZIP сохраняется при отказе. Служебный путь к промежуточной DLL в пакет не записывается.

EN: Package provenance contains member digests and relative source names. DLL/H5U/graphics bytes must still match the native build before sealing; changed resources are refused without replacing the previous ZIP. Private intermediate DLL paths are omitted.

The internal bank watcher can replace the SDK core while keeping its resident selector in the shared loader. It detaches the bank hook, observes four state counters, uses the existing core transfer/rollback transaction without loading the bank DLL into a generic plugin slot, then verifies the counters and restores the hook. An uncertain transport or state mismatch stops reactivation. This preserves the owned controller checks.

Live verification includes an ordinary named session with automatic core replacement, followed by a reversible native source edit that enables an additional export. The export was initially unavailable, then returned42 after automatic build/replace in the same game. Observed bank counters were zero before and after both replacements; this is not populated UI-cache retention. Historical resource-only runs do not prove bank HMR.

RU: в обычном нативном сеансе проверены автоматическая замена ядра и появление дополнительного export после обратимой правки исходника. Функция сначала отсутствовала, затем вернула42 в той же игре. Hook восстановлен, наблюдаемые счётчики остались нулевыми. Сохранение заполненного кеша карточки склепа проверено при обновлении DLL справочника и ядра SDK.
