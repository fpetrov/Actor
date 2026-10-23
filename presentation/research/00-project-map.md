# ACTOR: карта проекта для дальнейшей работы

Срез: 23 октября 2026 года, commit `aa5d46ec1fee4fbcab9292fc1f58f48fee3e1bb8`.

## Что проверено в источниках

Репозиторий GitHub: <https://github.com/fpetrov/Actor>. Локальный checkout: `/Users/fpetrov/PycharmProjects/Actor`. GitHub default branch — `main`.

GitHub recursive tree прочитан через подключенный GitHub API без `git fetch`. Его 1737 blob-объектов совпали с локальным `git ls-tree -r HEAD` по пути и SHA. Начальный и заключительный `git status --short` были пусты. Во время исследования исходный репозиторий не менялся нашими инструментами: pull, fetch, checkout, build, тесты и запуск модели не выполнялись.

Полный список отслеживаемых файлов с Git SHA и размерами находится в `repository-inventory.json`. Это опись всего дерева, а не утверждение о построчном аудите каждого файла. Исходники и документация изучены по подсистемам. Сгенерированные bindings, Unity metadata, бинарные модели, шрифты, длинные тестовые журналы и архивные JSON не требовали буквального чтения каждого байта. Перечни фактически изученных файлов и границы чтения есть в отдельных исследованиях.

## Суть

ACTOR — контракт и среда исполнения для внешних игровых агентов. Он не задает универсальные правила игры и не требует конкретной модели. Игра сама публикует типизированные возможности. Runtime согласует расширения, собирает контекст и управляет исполнением. Brain выбирает действия. Сервер игры определяет допустимость и результат.

Главный принцип: мышление отделено от непрерывного управления. Brain возвращает Skill или небольшой план. Skills могут двигаться, ждать, вести диалог и выполнять локальную механику без вызова большой модели на каждом кадре. Пересмотр приходит на границе действия или по обоснованному событию.

## Карта каталогов

| Каталог | Ответственность |
| --- | --- |
| `proto/actor/core` | Сессия, согласование, конверты и duplex bridge |
| `proto/actor/standard` | Необязательные движение и коммуникация |
| `proto/extensions/agents_arena` | Типы конкретной игры: комнаты, сигналы, кооперация, расследование, social, broadcast |
| `generated/` | C#/Python bindings, descriptors, manifests |
| `packages/com.actor.sdk` | Generic Unity SDK, регистрация типов, bridge/control, typed boundaries |
| `packages/com.actor.transport.grpc` | Unity gRPC-клиент и транспорт |
| `runtime/actor_runtime` | Сессия, WorldState, Brain, Skills, планы, события, память и episodes |
| `runtime/actor_codex` | Необязательный провайдер решений и комментатора через официальный SDK |
| `runtime/actor_jev` | Необязательный внешний классификатор Jev |
| `classifiers/minilm` | Локальный нейронный классификатор и его исследования |
| `runtime/actor_standard*` | Переиспользуемые Runtime-расширения |
| `runtime/actor_agents_arena` | Игровые Skills, policy, маршруты и механика Arena |
| `runtime/actor_broadcast` | Read-only observer, commentary/evidence, TTS, OBS, supervisor и operator UI |
| `Agents Arena/` | Unity-проект, серверные правила, сеть, игра, представление и редакторские сборщики |
| `src/Actor.ControlPlane` | .NET 10 HTTP API, SQLite, допуск и жизненный цикл серверов |
| `shared/com.actor.contracts` | Engine-independent подписанные admission/observer/resume credentials |
| `web/` | Статический сайт для списка и создания лобби; сама игра запускается в Unity |
| `scripts/` | Генерация, сборка, публикация, локальный запуск агентов и bounded summaries |
| `spikes/` | Контролируемые проверки интеграции, отказов, privacy, транспорта и release acceptance |
| `tests/`, `runtime/tests` | Серверные/SDK/Runtime регрессии и контрактные проверки |
| `art/` | Оригинальные редактируемые векторные мастера и воспроизводимая генерация |
| `docs/` | Архитектура и исторические отчеты с ограничениями каждого измерения |

## Соединения

```text
Внешний model provider
       ↕
Локальный Brain adapter
       ↕
ObjectiveRunner → SkillScheduler → RuntimeSession
       ↕
Python gRPC server ← loopback HTTP/2 stream → Unity gRPC client
                                                ↕
                                      SDK → Game Adapter
                                                ↕
                                    Unity client / NGO + UDP
                                                ↕
                                      Dedicated Game Server

HTTP: сайт/клиент → nginx → Control Plane → лобби/допуск/запуск сервера
```

gRPC не является соединением Python с VPS. Runtime и адаптер Brain находятся локально. Codex — внешний провайдер модели. MiniLM и Qwen TTS в текущей конфигурации локальны.

## Новые изменения

- `c8a29ef`: MiniLM v7, неблокирующий cold start, исполняемые планы и private traces.
- `aa5d46e`: привязка условных наблюдений к конкретному принятому плану и разрешение типов через согласованный registry.
- В недавней серии Broadcast реализован отдельный наблюдатель, подтверждение показанных кадров, фактологический AI-комментарий, голосовой worker, защищенное владение ресурсами и вывод в OBS/Twitch.
- Текущий TTS — Qwen3-TTS BF16 через MLX Audio. Это не Kokoro. Комментатор не является игровым участником с третьей скрытой ролью.

## Что особенно важно не перепутать

1. Core не содержит логику Arena. NGO принадлежит игре, а не generic SDK.
2. MiniLM не пишет план и не вызывается на каждом кадре. Входы ограничены событиями и выбранным свежим собственным наблюдением для принятого условия.
3. Rules — default classifier. MiniLM подключается явно и имеет статус development_only.
4. Состояние, сообщение игрока, гипотеза и доказанный итог имеют разный статус.
5. Локальное принятие команды, прием сервером и окончательное завершение — разные события.
6. В текущем Codex fresh ephemeral thread создается на decision; SDK-процесс ограничен Objective.
7. Game 0.10.0, Arena extension 0.7.0, Runtime 0.8.0, Core 1.0 и admission 4 имеют независимые версии.
8. Старые документы Social Deduction/Game 0.8/admission 3 не описывают текущий authenticated reconnect. Для него первичны `docs/reconnect.md` и исходники.
9. Исторические тестовые результаты не являются текущим приемочным прогоном.
10. Публичный Twitch достиг Live, но описанная attempt04 не прошла весь ресурсный gate. Полную стабильность эфира нельзя заявлять.

## Где продолжать исследование

- `01-core.md` — контракты, runtime, gRPC, permissions, память, управление.
- `02-classifier.md` — MiniLM, события, планы, Codex и ограничения оценок.
- `03-broadcast.md` — наблюдатель, evidence, голос, OBS, Twitch и фактическая приемка.
- `04-arena.md`, `05-runtime-extensions.md`, `06-core-supplement.md` — дополнительные карты исходников, если присутствуют.
- `design.md` — принципы оформления презентации.
- `qa.json` — выполненные проверки этой веб-презентации.

Работа с исследованием продолжится из этих файлов. Для обновления надо заново сверить GitHub HEAD, не менять checkout Actor и явно отличать новый срез от закрепленных источников этой версии.
