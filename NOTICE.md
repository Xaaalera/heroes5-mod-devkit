# Provenance / Происхождение

## RU

Инструменты выделены 24 сентября 2026 из собственной мастерской модов Xaaalera. Игровые бинарники и архивы не включены. Адреса и форматы — результаты исследования конкретной сборки; они не являются официальным SDK Nival.

Генератор terrain использует описание формата senyaak/homm5-editor: https://github.com/senyaak/homm5-editor/blob/main/docs/TERRAIN_FORMAT.md . Шаблон XDB берётся из установленного пользователем data.pak при локальной сборке, а не поставляется в репозитории.

Контракты .agents/review адаптированы из Xaaalera/claude-skills revision c8fec7777eb8e79c1e905be6d3296188c213870d. Review harness закреплён в package-lock.json. История исследований: https://xaaalera.github.io/heroes5-knowledge/reference/research-diary/ .

## EN

Extracted on September 24, 2026 from Xaaalera's own mod workshop. No game binaries or archives are bundled. Addresses/formats are build-specific research, not an official Nival SDK.

Terrain generation follows the linked homm5-editor format description. XDB templates are read from the user's installed data.pak during local generation, never bundled. Review contracts derive from the stated claude-skills revision; the harness is pinned in package-lock. Research history is linked above.
