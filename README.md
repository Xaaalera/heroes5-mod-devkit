# xkit — Xaaalera toolkit SDK

## RU

xkit помогает разрабатывать моды Heroes V Universe: создавать проекты, применять поддерживаемые изменения исходников в тестовой игре и выпускать отдельные пакеты для игроков. C++ автоматически компилируется в промежуточную DLL; вручную переносить её не нужно.

## EN

xkit supports Heroes V Universe mod development: create projects, apply supported source changes in a test game and release independent player packages. C++ is automatically compiled into an intermediate DLL.

## Документация / Documentation

Единственный источник текущих руководств — наш сайт. / Our website owns the living documentation.

- [Начать работу с xkit](https://xaaalera.github.io/heroes5-knowledge/modding/devkit/) · [English guide](https://xaaalera.github.io/heroes5-knowledge/en/modding/devkit/).
- [Консоль, диагностика и хранение / Console, diagnostics and storage](https://xaaalera.github.io/heroes5-knowledge/modding/console/).
- [Команды SDK / SDK commands](https://xaaalera.github.io/heroes5-knowledge/reference/xkit-commands/).
- [Game API](https://xaaalera.github.io/heroes5-knowledge/reference/game-api/).
- [Карта исследований / Research index](https://xaaalera.github.io/heroes5-knowledge/reference/research-index/).
- [Готовые выпуски / Releases](https://github.com/Xaaalera/heroes5-mod-devkit/releases).
- [Исходники xkit / xkit source](https://github.com/Xaaalera/heroes5-mod-devkit).

Поддерживается исследованная сборка Universe на Windows 10/11. Игра устанавливается отдельно; готовые архивы SDK содержат исходники и служебные DLL. Точные требования и границы HMR указаны в руководстве. Каждый мод выпускается отдельно; общая инфраструктура указывается как зависимость.

Supports the studied Universe build on Windows 10/11. Install the game separately; SDK archives contain source and infrastructure DLLs. The guide states requirements and HMR boundaries. Mods ship separately with their stated shared dependencies.

## Standalone use / Работа вне мастерской

Репозиторий можно использовать отдельно. После клонирования выполни `git submodule update --init game-api`, затем следуй инструкции установки на сайте. В общей мастерской используй её `sync-subrepos`, чтобы сохранить один checkout каждой зависимости. Рабочие правила — в [AGENTS.md](AGENTS.md).

Use this repository independently. After cloning, run `git submodule update --init game-api`, then follow the site's installation guide. In the shared workshop, use its canonical dependency synchronization.

## Проекты и контакты / Projects and contacts

- [Game API source](https://github.com/Xaaalera/heroes5-game-api).
- [Предиктор / Deployment predictor](https://github.com/Xaaalera/heroes5-deployment-preview).
- [Справочник хранилищ / Bank reference](https://github.com/Xaaalera/heroes5-bank-reference).
- [Исходники базы / Knowledge source](https://github.com/Xaaalera/heroes5-knowledge) · [сайт / website](https://xaaalera.github.io/heroes5-knowledge/).
- [Стандарты / Code standards](https://github.com/Xaaalera/claude-skills).
- [Universe / Heroes Lobby](https://h5lobby.com/).

[Xaaalera](https://github.com/Xaaalera) · [email](mailto:dampirsimpl@gmail.com) · [Telegram](https://t.me/Victima). Наши инструменты не являются официальными продуктами Universe. / Our tools are unofficial Universe projects.
