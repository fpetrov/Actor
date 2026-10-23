# ACTOR: исследование ядра, контрактов и игровой архитектуры

Дата: 23 октября 2026 года. Рабочий источник: `/Users/fpetrov/PycharmProjects/Actor`.
Режим: только чтение исходников; никаких pull/fetch/build/test или изменений в Actor не выполнялось. Этот отчет создан отдельно в `/private/tmp` по поручению основного агента.

Основной агент отдельно сообщил о сравнении GitHub recursive tree с локальным `git ls-tree HEAD`: 1 737 blob-объектов совпали на `aa5d46e`. Это результат основного агента, а не повторная проверка данным исследователем. Чтение происходило из живого checkout, в котором параллельно работает другой агент; номера строк относятся к наблюдавшейся версии.

## 1. Суть продукта

ACTOR — инфраструктура подключения взаимозаменяемых агентных runtime к играм через типизированные, версионируемые capabilities. Игра объявляет, какие наблюдения, события и команды доступны конкретному подключению; Runtime обнаруживает их, собирает совместимый каталог Skills и дает Brain только этот каталог и разрешенное состояние. Это не универсальная игровая логика и не модель мира, зашитая в LLM.

Ключевое разделение ответственности:

- **Brain** решает, что делать: выбирает один Skill, ограниченный план или завершение Objective.
- **Runtime** организует исполнение: валидация, очереди событий, память, ограничения контекста, один foreground Skill, владение командами, прерывание и cleanup.
- **SDK / Game Adapter** переводит типизированные capabilities в существующий игровой ввод и разрешенные игровые данные.
- **Authoritative Dedicated Server** определяет физику, доступность действий, видимость, роли, смену фаз, результаты и победителя.
- **Agents Arena** — конкретная reference game / испытательная среда с социальным взаимодействием и скрытой информацией. Ее сетевой стек NGO и ее комнаты не являются частью generic Core.

Основания: `README.md:3`, `README.md:7`, `README.md:9`; `proto/actor/core/v1/actor.proto:7`; `runtime/actor_runtime/skills.py:1`; `Agents Arena/Assets/Scripts/Actor/AgentsArenaGameAdapter.cs:14`.

Слово **Body** удобно для объяснения слоя исполнения и игрового персонажа, но в текущем исходном API основными именами являются `ActorSkill`, `SkillScheduler`, `IActorGameAdapter`, `AgentInputSource`, `NetworkCharacter`. Нельзя выдавать триаду Brain/Body/Runtime за три конкретных одноименных класса.

## 2. Полная цепочка соединений

```text
Пользовательская цель
        ↓
Brain / optional Codex provider
        ↓ SkillCall или SkillPlan
ObjectiveRunner → SkillScheduler → typed RuntimeSession.command
        ↓
Локальный authenticated gRPC HTTP/2 duplex stream
        ↓
ACTOR SDK ActorBridgeClient.Pump на игровом потоке
        ↓
AgentsArenaGameAdapter → AgentInputSource / NetworkSocialMatch
        ↓
Обычный Unity game client → NGO + Unity Transport / UDP
        ↓
Unity Dedicated Server → physics + game authorities
        ↓ разрешенные owner views / events / raw owner snapshot
Game Adapter → gRPC → RuntimeSession → WorldState
        ├→ EventRouter → advisory classifier → review / interrupt policy
        ├→ private WorkingKnowledge
        └→ selected public episodic SQLite evidence
                    ↓
            bounded ContextBuilder → следующий Brain decision
```

**gRPC не соединяет агент напрямую с VPS.** Python Runtime слушает `127.0.0.1`; Unity выступает gRPC-клиентом. К VPS идет отдельное игровое NGO/UDP-соединение. Через него одинаково играют человек и агент. Это самая важная техническая деталь для архитектурного слайда.

Источник: `runtime/actor_runtime/server.py:24`, `server.py:52`, `server.py:63`; `packages/com.actor.transport.grpc/Runtime/GrpcActorTransport.cs:23`; `proto/actor/core/v1/bridge.proto:8`; `Agents Arena/Assets/Scripts/Actor/ActorControlController.cs:208`; `Agents Arena/Assets/Scripts/Networking/NetworkCharacter.cs:149`.

Control Plane образует отдельную цепочку запуска:

```text
Browser или Unity lobby → nginx → .NET Minimal API → LobbyManager
                                                   ↓
                                     LocalProcessGameServerAllocator
                                                   ↓
                                      отдельный Unity server на UDP-порту
```

Веб-сайт создает/показывает комнаты и копирует код, сама игра запускается в Unity. Control Plane не вычисляет физику и не подсказывает агенту скрытые роли.

## 3. Уровни протокола и расширяемость

| Уровень | Содержимое | Исходники |
|---|---|---|
| Core 1.0 | identity/session, handshake, manifest, Any envelopes, ordering, heartbeat, command result, errors | `proto/actor/core/v1/actor.proto`, `bridge.proto` |
| Standard motion 0.1.0 | Transform2D, Motion2D, Locomotion2D | `proto/actor/standard/v1/` |
| Standard communication 0.1.0 | send, owner message, delivery receipt, permitted peers | `proto/actor/standard/communication/v1/communication.proto` |
| Agents Arena 0.7.0 | rooms, station signals, calibration, incidents, social deduction, broadcast | `proto/extensions/agents_arena/v1/` |

`CapabilityDefinition` содержит identity/version/kind, input/output type URL, extension identity и семантику доставки команд. Envelopes используют `google.protobuf.Any`; canonical type URL — identity вида `type.googleapis.com/<full proto name>`, а не URL для скачивания схемы. Required extension должен точно совпасть по версии и SHA-256 fingerprint. Optional неизвестное расширение может остаться opaque. Нет автоматической загрузки произвольного кода.

Источники: `proto/actor/core/v1/actor.proto:28`, `actor.proto:36`, `actor.proto:47`; `runtime/actor_runtime/extensions.py:115`, `extensions.py:188`, `extensions.py:215`; SDK `Runtime/Registry/ExtensionPackage.cs:23`, `ExtensionPackage.cs:62`.

SDK выводит типы, manifest и Any из `RegisterObservation<T>`, `RegisterCommand<T>`, `RegisterEvent<T>`; нельзя регистрировать несовпадающий compiled descriptor. Python умеет использовать известные сгенерированные классы или построить dynamic class в изолированном descriptor pool. Генератор и версии официальных protoc/grpc зафиксированы, generated artifacts лежат в Git; это не handwritten serialization.

Источники: `packages/com.actor.sdk/Runtime/Registry/AdapterContracts.cs:32`; `ActorCapabilityRegistry.cs:143`; `ExtensionPackage.cs:62`; `runtime/actor_runtime/extensions.py:145`; `proto/README.md`.

Установленные entry points отделяют Core от игры и провайдера: `actor.extensions`, `actor.skills`, `actor.brains`, `actor.match_policies`, `actor.event_rules`, `actor.objective_finalizers`, `actor.classifiers`, `actor.games`. Полный перечень находится в `runtime/pyproject.toml:25` и далее.

## 4. Session, handshake и надежность исполнения

Один RPC `ActorBridge.Connect` содержит два независимых потока. Unity создает session UUID и возрастающие sequence numbers, сначала отправляет Hello и CapabilityManifest. Runtime проверяет protocol major, идентичность Hello/manifest, типы capabilities, extension version/hash; только затем отправляет accepted RuntimeHello. До принятия handshake команды не исполняются.

Источники: `proto/actor/core/v1/bridge.proto:8`; `packages/com.actor.sdk/Runtime/Bridge/ActorBridgeClient.cs:94`; `runtime/actor_runtime/session.py:73`, `session.py:163`.

Per-launch credential передается обоим процессам через окружение, в metadata `x-actor-bridge-token`. Endpoint принимает только loopback HTTP без пути/credentials; bridge не публикуется через nginx. Авторизация проверяется constant-time сравнением. Это локальная граница взаимодействия, не шифрование публичного игрового UDP.

Источники: `Runtime/Bridge/TransportContracts.cs:21`; `GrpcActorTransport.cs:47`; `runtime/actor_runtime/server.py:63`; `runtime/actor_runtime/control.py` — генерация `secrets.token_hex(32)`.

`ActorBridgeClient.Pump` — единственная точка, где вызываются adapter capture/command handlers. Она привязана к потоку, создавшему клиент. Background reader/writer работают с Protobuf и bounded mailboxes. Native transport cleanup выводится из frame lock.

Источники: `ActorBridgeClient.cs:81`, `ActorBridgeClient.cs:139`, `ActorBridgeClient.cs:536`; `GrpcActorTransport.cs:114`.

Default частоты и границы, которые можно показывать только как настройки/реализацию, не как измеренный latency claim:

- SDK observations — 15 Hz, configurable 1–60 Hz.
- Runtime navigation loop — 20 Hz по документированному Arena Skill.
- Owner movement refresh и server snapshots — до 30 Hz; press/release/direction change отправляется сразу.
- Физика игры — FixedUpdate, в документации 50 Hz.
- Wire message limit — 1 MiB.
- Latest commands coalesce вместо очереди устаревшего движения; ordered/control остаются bounded.
- Movement intent перестает действовать через 0.25 с без обновления.
- Команды имеют TTL и issue time; TTL повторно проверяется непосредственно перед dispatch на игровом потоке.

Источники: `Runtime/Bridge/TransportContracts.cs:56`; `ActorBridgeClient.cs:337`; `ActorCapabilityRegistry.cs:90`; `runtime/actor_runtime/mailbox.py:9`; `runtime/actor_runtime/session.py:230`, `session.py:293`; `AgentInputSource.cs:13`; `NetworkCharacter.cs:23`.

`CommandResult.Accepted` означает «локальный adapter принял намерение». Успешное игровое действие устанавливается только correlated authoritative event/observation. Ошибка, overflow, disconnect и смена session очищают pending work; старые задачи не получают права управлять новой session.

## 5. Brain, Skills и планы

Brain API: `start`, `decide`, `interrupt`, `close`; необязательный `interrupt_decision` отменяет только текущий decision, сохраняя Objective. Provider выбирается entry point, а не условием Unity/Codex внутри Core. `SequenceBrain` использует тот же production Runner/Scheduler и служит deterministic reference provider.

Источник: `runtime/actor_runtime/brains.py:62`, `brains.py:145`, `brains.py:186`.

Текущий strict union решения:

1. `SkillCall(kind="skill", skill_id, arguments)`.
2. `SkillPlan(kind="plan", steps, conditions, fallback)`.
3. `FinishObjective(kind="finish", status, reason)`.

`SkillPlan`: 1–4 последовательных шага, не более двух one-shot conditions, optional fallback. Condition — только `nonempty` у объявленного repeated/string поля negotiated decoded observation/event. Никакого исполнения произвольного кода или natural-language predicate. Все calls валидируются при принятии и повторно Scheduler перед исполнением. После failed step остаток sequence отбрасывается; fallback выполняется максимум один раз. Response condition заменяет остаток плана, сначала полностью останавливая/очищая foreground Skill.

Источники: `brains.py:20`, `brains.py:28`, `brains.py:36`, `brains.py:52`; `plans.py:13`, `plans.py:84`, `plans.py:110`; `objectives.py:297`, `objectives.py:459`.

На уровне Skill:

- `SkillDefinition`: strict Pydantic input/result, capability requirements, owned commands, deadlines, result privacy policy, availability.
- `SkillContext`: typed observation/event reads, freshness, commands по lease, cancellable waits; нет raw transport или mutable WorldState API.
- `SkillScheduler`: один foreground Skill на session, даже при разных Scheduler instances. Проверка capabilities/availability/preflight до acquisition lease.
- При любом terminal path нормальные command rights отзываются **до** cleanup; отдельная cleanup task кратко сохраняет право обнулить ровно те команды, которыми Skill владел. После нее lease освобождается.

Источники: `skills.py:80`, `skills.py:283`, `skills.py:410`, `skills.py:460`, `skills.py:498`, `skills.py:536`.

LLM не управляет кадрами и не вызывается на каждом движение/ожидание. Исполняемый Skill может работать секунды и отправить сотни typed intents; Brain вызывается после его завершения или обоснованного strategic review. Последовательные plan steps исполняются без дополнительного Brain turn.

## 6. События, классификатор и перепланирование

`RuntimeSession.accept` декодирует разрешенные данные и заполняет один WorldState; для events bounded ingress в EventRouter происходит раньше diagnostic historical ring. Receive path не ждет SQLite или network model. Installed event rules назначают priority, criticality, semantic identity, aggregation и data privacy; строка игрока не может сама объявить себя критичной.

Источники: `session.py:108`, `session.py:126`; `events.py:163`, `events.py:259`.

ClassificationContext содержит Objective, current Skill, максимум 24 own observation entries и точный snapshot принятого current plan (`null` при отсутствии). Router дает provider совет, но сам определяет итоговые review/cancel/persist/cooldown semantics. Mandatory system review не отменяется нейросетью; uncertain critical assessment дает conservative review. Advisory failure/timeout возвращается к rule classifier.

Источники: `events.py:130`, `events.py:304`, `events.py:355`; `runtime/actor_runtime/classifiers.py`; `docs/executable-plans.md`.

Главная современная оговорка: обычные observation frames не классифицируются. **Исключение добавлено для принятого conditional plan**: он может номинировать первое свежее own observation, удовлетворяющее condition. План показывает классификатору уже предусмотренную реакцию: routine событие может запустить локальную response без LLM, review пересиливает план и возвращает управление Brain. Critical phase/life/game-over handling сохраняется независимо от этой оптимизации.

Источники: `session.py:112`; `plans.py:48`; `events.py:325`; `actor_agents_arena/social_agent.py:111`.

У strategic review меняется `cognitive_revision`, у движения — нет. Runner сравнивает revision перед принятием ответа и еще раз непосредственно перед dispatch. Устаревший ответ отменяется/дренируется, не может начать Skill. Сохраняется один active decision; burst событий сводится к следующему актуальному snapshot. Soft preemption сохраняет Objective, но останавливает Skill с cleanup; terminal cancellation завершает Objective.

Источники: `events.py:337`; `objectives.py:289`, `objectives.py:344`, `objectives.py:362`, `objectives.py:396`.

Default bounded policy: queue/pending 128, dedup keys 4096, event 16 KiB, aggregation/cooldown 2 с, max replans 8. Protected critical overflow переводит routing в degraded state и safely fails Objective вместо тихой потери evidence. Эти числа — configuration defaults, не performance benchmark.

Классификатор MiniLM и Broadcast изучаются отдельными агентами; данный отчет утверждает только integration boundary, проверенную в Core.

## 7. Контекст и три различных вида состояния

### WorldState

Latest-value cache разрешенных observations на одну session; bounded recent event ring и typed inboxes для еще не consumed events. Protobuf copy предотвращает случайную мутацию хранимого evidence. Никакого второго скрытого world cache или общего между агентами состояния.

Источник: `world.py:32`, `world.py:44`, `world.py:55`, `world.py:89`.

### WorkingKnowledge

Private in-process память между bounded episodes одного матча. Различает `historical_observation`, `player_claim`, `hypothesis`, `contradiction`. Каждая запись имеет source/sequence/revision, исходное время и expiry. Потеря видимости не обновляет last-seen. Модель может сохранить короткую непроверенную гипотезу или противоречие с evidence references; это не превращает чужую реплику в истину. Exact evidence versions замораживаются при цитировании, dependencies хранятся/удаляются вместе; notes не переживают evidence expiry.

Источники: `knowledge.py:17`, `knowledge.py:36`, `knowledge.py:111`, `knowledge.py:195`; `actor_agents_arena/social_agent.py:141`; `knowledge_skills.py`.

### Episodic SQLite

Локальная bounded история выбранных public events, safe SkillResults, Objective outcomes. Default in-memory SQLite session-local; persistence требует одновременно path и stable local profile. Network ActorId не используется как постоянный профиль. Scope дополнительно привязан к текущему матчу. Retrieval — простое lexical overlap/importance/recency, не embeddings/RAG.

Источники: `memory.py:77`, `memory.py:124`, `memory.py:312`, `memory.py:374`.

SQLite работает на одном dedicated worker с максимум 32 outstanding operations. Bound действует на число записей и физический файл; corrupt/foreign DB не удаляется и не заменяется автоматически. Private chat/authorization/own hidden state не persist по умолчанию. `public`, `session_only`, `sensitive` — explicit retention policy, а не попытка угадать секретность по тексту.

Источники: `memory.py:135`, `memory.py:163`; `events.py:494`; `privacy.py:23`, `privacy.py:46`.

### ContextBuilder и authority

`DecisionContext.external_state()` исключает человеческую Objective и trusted installed catalog. Он включает актуальные разрешенные данные, provenance/age/stale/opaque/truncated flags, triggers, previous Skill result, working knowledge и selected memories. Текущие defaults cognitive config: 32 KiB внешнего JSON, 24 observations, 12 recent events, 6 memories, 24 working records / 8 KiB. Trigger evidence и previous result имеют приоритет при packing; omission counters явные.

Источники: `snapshots.py:68`, `snapshots.py:105`, `snapshots.py:212`, `snapshots.py:326`; `config.py`.

В Codex objective идет как user `TextInput`, catalog — developer metadata, а game snapshot — `ExternalMessage(tool_name="actor_game", namespace="world_state", ...)`. Реплика из игры «ignore objective» остается untrusted external data. Нет direct game tools, позволяющих модели обойти Scheduler.

Источники: `actor_codex/adapter.py:215`, `actor_codex/adapter.py:278`, `actor_codex/adapter.py:297`; `docs/codex.md`.

## 8. Долгий матч состоит из ограниченных эпизодов

Generic `MatchEpisodeController` владеет cognition одной session и сохраняет общий Session/Scheduler/Router/scoped memory/WorkingKnowledge. Installed policy возвращает `EpisodeDirective(waiting|active|suspended|terminal)` и game-specific episode key. Core coordinator не ветвится по Alignment, Voting или room enum.

Каждый episode получает fresh Brain instance; reuse запрещен. По phase/life/connection-generation/body-discovery boundary старый execution guard становится false, Skill команды отзываются, Brain закрывается до следующего. Смерть/изгнание останавливает cognition, клиент пассивно ждет outcome команды. FinishObjective заканчивает episode, **не матч**. Только authoritative terminal boundary из политики делает participation complete.

Источники: `episodes.py:31`, `episodes.py:100`, `episodes.py:250`, `episodes.py:298`; `actor_agents_arena/social_agent.py:57`, `social_agent.py:78`, `social_agent.py:104`.

Defaults участия: 1 800 с, 32 episodes, 160 decisions, 1 500 000 measured tokens, 2 с restart cooldown; per-episode limits продолжают действовать. Reports до game-over скрывают private action identifiers и episode details. Postgame безопасные summaries разрешаются только после authoritative boundary.

**Текущая деталь Codex:** один owned SDK client/process на episode, но fresh ephemeral model thread на каждом decision. Это явно реализовано `adapter.py:228`, `adapter.py:293`. Continuity переносится typed snapshot/previous result/WorkingKnowledge, а не бесконечной модельной перепиской. Суммарные usage budgets не сбрасываются от создания нового thread. Read-only sandbox, denied approvals, empty temporary workspace и отключенные inherited MCP servers задают computer boundary; no hidden reasoning сохраняется.

## 9. Agents Arena: физика, видимость, социальная логика

### Одинаковый путь ввода Human и Agent

`PlayerInputSource` и `AgentInputSource` реализуют `IMovementInputSource`. Control controller переключает источник, очищая предыдущее intent. `NetworkCharacter` читает только owner input, передает normalized movement intent через owner-only unreliable RPC. Server проверяет finite values, sequence, rate и timeout; client position/velocity как simulation command не принимает.

`ActorCharacter → CharacterMotor2D → Rigidbody2D` исполняется на сервере. На client motor disabled. Owner `Visual` предсказывается через `OwnerMovementPrediction`; network root остается server-controlled. Post-physics owner snapshots содержат position/velocity/facing и реально simulated input sequence/duration. Adapter экспортирует coherent raw authoritative snapshot, не смешивает предсказанное положение со старой скоростью. Это сохраняет responsive presentation и честную поверхность наблюдений для агента.

Источники: `ActorControlController.cs:178`, `ActorControlController.cs:190`; `AgentInputSource.cs:8`; `NetworkCharacter.cs:60`, `NetworkCharacter.cs:76`, `NetworkCharacter.cs:130`, `NetworkCharacter.cs:149`, `NetworkCharacter.cs:201`; `AgentsArenaGameAdapter.cs:59`.

### Серверная истина и player-specific views

`SocialMatchSession` связывает реальные approved connections, capsule-center poses, комнаты/терминалы с pure `SocialMatchAuthority`. `NetworkSocialMatch` — private owner endpoint; он не публикует Alignment/tasks/global life count в Everyone NetworkVariable. Commands аутентифицируются по конкретному owned player object, server derives caller; содержат match ID, phase revision, expiry, correlation и connection generation.

Authority повторно проверяет phase/life/permission, current visibility/range, cooldown, replay conflict и rate. Это необходимо даже при client-side Skill preflight. Work-start receipt не означает task completion: нужно удерживать physical interaction и выполнить механические шаги. Есть MaintenanceHold, CircuitCalibration, SensorObservation; fake work внешне нейтрально и не раскрывает Alignment.

Источники: `SocialMatchSession.cs:159`, `SocialMatchSession.cs:215`; `NetworkSocialMatch.cs:10`, `NetworkSocialMatch.cs:43`, `NetworkSocialMatch.cs:68`; `SocialMatchAuthority.cs:363`, `SocialMatchAuthority.cs:499`, `SocialMatchAuthority.cs:517`, `SocialMatchAuthority.cs:571`, `SocialMatchAuthority.cs:602`.

Visibility фильтруется до SDK: radius/line-of-sight используются и для NGO observer sets, и для allowed action checks. Hidden actors/bodies не реплицируются всем с последующим сокрытием UI-туманом. `SocialVisibilitySystem.RefreshNow` вызывает NetworkHide/NetworkShow перед NGO network tick; policy failure закрывает видимость. Owner endpoint переживает death/meeting, чтобы получить terminal result.

Источники: `SocialVisibilitySystem.cs:71`, `SocialVisibilitySystem.cs:90`, `SocialVisibilitySystem.cs:129`, `SocialVisibilitySystem.cs:157`; `SocialMatchSession.cs:136`, `SocialMatchSession.cs:144`; `SocialMatchAuthority.cs:302`.

### Социальный матч

Параметры reference mode: 4–6 игроков, один Impostor, Crew и независимые duties Engineer/Sheriff. Сервер самостоятельно случайно распределяет affiliation и duty. Клиент не выбирает seed/hidden assignment. Fixed phases: WaitingForPlayers → RoleAssignment → Exploration → Discussion → Voting → VoteResolution → Exploration или GameOver.

Crew побеждает через task threshold или elimination всех Impostors; Impostor — parity или просроченную unrepaired sabotage. Действующие правила и точные deadline-based transitions полностью server-owned. Own alignment/duty/tasks приходят owner-only, чужие утверждения — claims. Видимое убийство получает только фактический witness; victim presentation намеренно не раскрывает attacker ID. Individual ballots private до resolution. Полные assignments/replay summary открываются после GameOver.

Источники: `SocialMatchAuthority.cs:114`, `SocialMatchAuthority.cs:406`, `SocialMatchAuthority.cs:423`, `SocialMatchAuthority.cs:465`, `SocialMatchAuthority.cs:479`, `SocialMatchAuthority.cs:644`, `SocialMatchAuthority.cs:654`.

### Коммуникация и независимость агентов

Обмен идет через обычный game server и targeted owner RPC, никакого второго agent-to-agent socket. Command не содержит trusted sender: сервер выводит его из connection. Существуют четыре разные стадии: adapter accepted → server accepted → actual receiving-client ACK → Brain consumed. Последнее не доказывается сетевой квитанцией.

Sandbox поддерживает direct/station; Social — direct/proximity/discussion с phase/life/visibility restrictions. Private findings в asymmetric investigation передаются партнеру как sensitive direct message, а не общая память. Каждый агент имеет отдельные Python process, Unity client, token/port, WorldState, provider resources, memory database/profile. Supervisor задает objective и следит за cleanup/terminal proof; не переносит роли или game data между агентами.

Основания: `docs/communication.md`, `docs/multi-agent.md`, `AgentsArenaGameAdapter.cs:141`, `docs/networking.md` (архитектурные разделы); installed Communication Skills используют тот же RuntimeSession.

## 10. Control Plane и admission

`.NET 10` API регистрирует singleton LobbyManager, SQLite repository, process allocator, port allocator и hosted lifecycle service. Public `/api/v1` включает list/create/get/join/resume/game-version. Observer admission вынесен в закрытый internal route и требует отдельный operator key/direct loopback boundary.

Источники: `src/Actor.ControlPlane/Program.cs:9`; `Api/Endpoints.cs:25`, `Endpoints.cs:37`, `Endpoints.cs:49`.

Создание комнаты валидирует версии, mode, capacity; сохраняет immutable rules snapshot, резервирует уникальный UDP port и случайный 32-byte match secret, запускает отдельный native Unity server. Secret идет только в environment; SQLite хранит metadata/PID/start time, не signing keys. Default capacity — 5 servers на 7777–7781. Сервер присылает authenticated ready/heartbeat/finished callback по loopback HTTP. Lifecycle следит за startup timeout/heartbeat/process/empty room и освобождает порт после остановки процесса.

Источники: `Application/LobbyManager.cs:87`, `LobbyManager.cs:295`, `LobbyManager.cs:368`, `LobbyManager.cs:404`; `Infrastructure/LocalProcessGameServerAllocator.cs:15`; `Configuration/MatchOptions.cs:5`.

Join выдает short-lived HMAC ticket с match/version/player/nonce/actor/generation. Pending tickets резервируют capacity. NGO approval независимо проверяет подпись, claims, replay, capacity. Current source реализует authenticated Social resume с private rotating credential / connection generation; совпадение display name не восстанавливает actor.

Источники: `LobbyManager.cs:142`, `LobbyManager.cs:176`; `Agents Arena/Assets/Scripts/Networking/MatchSession.cs:337`; `NetworkSocialMatch.cs:108`.

На restart Control Plane ранее активные persisted rooms reap/mark Failed: match signing keys не persist, durable live match recovery нет. Lobby statuses Starting/Ready/Running/Stopping/Finished/Failed — состояния allocator, они не равны Social phases.

Источники: `LobbyManager.cs:57`; `Domain/Lobby.cs:5`.

Broadcast observer имеет отдельную registry/admission, не создает player object и не занимает player capacity/slot/RNG assignment. Это подтверждает, что commentator/observer — не скрытая игровая роль и не обычный player с elevated omniscience. Подробная Broadcast privacy chain — в отдельном отчете.

Источники: `LobbyManager.cs:210`; `MatchSession.cs:345`.

## 11. Актуальность документации и границы доказательств

1. `README.md:42`, runtime pyproject, generated manifests и текущий MatchOptions сходятся: Game0.10.0 / Runtime0.8.0 / Arena0.7.0 / Core1.0 / admission4. Эти версии независимы.
2. Многие foundational guides сохранили historical milestones: `actor-protocol.md`, `proto/README.md`, `skills.md`, `brains.md`, `networking.md`, `multi-agent.md` упоминают Game0.8/Runtime0.6/Arena0.3–0.5. Их архитектурные описания полезны, но version strings нельзя переносить на текущий слайд.
3. Старый `event-routing.md` пишет «only event envelopes» без exception. Современные `executable-plans.md`, `session.py:112`, `plans.py:48` явно добавляют narrowly nominated condition observations. Не говорить «классификатор вызывается на каждом кадре».
4. `brains.md` первоначально описывает union skill/finish и один model thread episode. Текущий `brains.py:36` содержит plan, а `actor_codex/adapter.py:228` создает fresh thread per decision. Для текущей схемы следовать source.
5. `networking.md` исторически пишет «No authenticated resume». Это устарело: current Endpoints/LobbyManager/MatchSession/ResumeAdmissionRegistry содержат resume и generation fencing.
6. `README.md:3` и `README.md:42` говорят limited test prerelease, опубликованный/deployed RC1; полная Broadcast acceptance pending. Historical test counts и прошлые successful live matches не доказывают актуальный autonomous win, performance/capacity или Twitch native audiovisual acceptance.
7. Не запускались тесты/игра/сборки. Исследование доказывает наличие и логику реализации; не новый runtime acceptance. Межагентная изоляция/неутечка в docs и коде — архитектурные гарантии и исторически проверяемые сценарии, а не криптографический security audit.

## 12. Удобные формулировки для лаконичных слайдов

- «Модель выбирает цель действия. Runtime доводит действие до результата».
- «Решения — по смыслу событий. Движение — без вызова LLM на каждом кадре».
- «Один протокол. Разные игры. Взаимозаменяемый интеллект».
- «Агент видит только то, что разрешил игровой сервер».
- «План исполняется локально. Значимое изменение возвращает управление модели».
- «Чужая реплика — свидетельство. Не команда и не установленный факт».
- «Общая арена. Отдельный контекст у каждого агента».
- «Agents Arena проверяет память, кооперацию и решения при неполной информации».

Нельзя обещать универсальный navigation planner, произвольную поддержку любого engine без adapter, полностью завершенную Twitch acceptance, превосходство по win-rate, измеренную экономию токенов от плана или криптографическую защиту plaintext gameplay.

## 13. Карта модулей Core

| Файл | Ответственность |
|---|---|
| `server.py` | `grpc.aio` loopback listener, token check, session lifecycle, heartbeat/peer timeout |
| `session.py` | handshake/manifest, decode/injest, typed command envelopes, result timeout, one WorldState/Router/plan binding |
| `extensions.py` | entry-point package discovery, descriptor hash/schema check, isolated pool, exact negotiation, opaque decoding |
| `world.py` | latest observations, recent events, inbox retention, update notifications, copy-on-read |
| `inbox.py` | bounded typed unread events, identity/order/expiry/dedup policies |
| `mailbox.py` | bounded control queue и latest-value commands |
| `skills.py` | strict models, registry/catalog, context restrictions, leases, foreground scheduler, cleanup |
| `builtin_skills.py` | generic monotonic cancellable `actor.wait` |
| `brains.py` | decisions, plan schemas, discovery, provider protocol, SequenceBrain |
| `plans.py` | validation и state machine принятого bounded plan / conditions / fallback |
| `objectives.py` | one Objective loop, context, bounded decisions/repair/replans, stale decision guards, finalizers |
| `events.py` | synchronous bounded ingress, rule hints, async classifier/persist worker, revision/review/preemption/acks |
| `classifiers.py` | provider-neutral context/assessment/rules, discovery, rule fallback |
| `snapshots.py` | reflection/context selection, provenance/privacy/expiry/truncation, UTF-8 budget packing |
| `memory.py` | scoped bounded SQLite episodic evidence on one worker, lexical retrieval, safe administration |
| `knowledge.py` | private scoped working observations/claims/notes, evidence dependency versions, expiry/budget |
| `knowledge_skills.py` | explicit remember-hypothesis / remember-contradiction typed local procedures |
| `privacy.py` | explicit data policies, public projections, known-secret redaction, hidden activity masking |
| `episodes.py` | policy-driven multi-episode participation, fresh Brain lifecycle, global budgets/guards |
| `telemetry.py` | whitelisted provider aggregate usage/tool/lifecycle counters with unknown coverage retained |
| `config.py` | bounded validated local cognition/router/context/memory/participation settings |
| `cli.py` | installed serve/scripted/agent/match/auth/memory workflows; resource shutdown/reporting |
| `launching.py` | installed game recipes, client manifest/version/hash, process identity/ownership records |
| `control.py` | auth-free doctor, installed join/status/stop, credential generation, exact owned child cleanup |
| `errors.py` | structured ActorError |

SDK source groups: engine-neutral registry/adapter/extension package; engine-neutral bridge/pump/options; separate concrete Grpc transport. Unity integration: ActorControlController + AgentInputSource + AgentsArenaGameAdapter + game-specific bindings. Server integration: MatchSession/admission, NetworkCharacter/motor/prediction, SocialMatchSession + pure authority + targeted endpoint + observer visibility system.

## 14. Фактический охват чтения

Полностью или последовательными sections прочитаны:

- `README.md`; `docs/actor-protocol.md`, `python-runtime.md`, `extensions.md`, `skills.md`, `brains.md`, `executable-plans.md`, `event-routing.md`, `context-builder.md`, `memory.md`, `match-lifecycle.md`, `networking.md`, `social-deduction.md`, `communication.md`, `multi-agent.md`, `codex.md`; `proto/README.md`.
- Оба Core proto и все три Standard motion proto, metadata/examples через architecture guides. Остальные game/communication proto были инвентаризированы; семантика сопоставлена bindings/guides, но полный построчный разбор всех Arena proto в этот subtask не входил.
- Core runtime: `brains.py`, `plans.py`, `session.py`, `server.py`, `events.py`, `objectives.py`, `skills.py`, `world.py`, `extensions.py`, `snapshots.py`, `episodes.py`, `knowledge.py`, `memory.py`, `config.py`, `mailbox.py`, `control.py`, `launching.py`, `privacy.py`, `inbox.py`, `knowledge_skills.py`, `builtin_skills.py`, `telemetry.py`; `classifiers.py` — interface integration scope. Отдельные tool outputs достигали лимита; критичные sections повторно прочитаны точечными диапазонами, но это не побайтовое подтверждение чтения всех generated/test/cache files.
- SDK: `AdapterContracts.cs`, `TransportContracts.cs`, `ActorBridgeClient.cs`, `ActorCapabilityRegistry.cs`, `ExtensionPackage.cs`, `GrpcActorTransport.cs`.
- Control Plane: Program, Endpoints, LobbyManager, infrastructure/configuration/domain/lifecycle source groups. Его исходники прочитаны без сборки и без API mutation.
- Unity: AgentsArenaGameAdapter, Social/Investigation bindings, AgentInputSource, ActorControlController (runtime control paths), NetworkCharacter, ActorCharacter, CharacterMotor2D, ICharacterMotionState, SocialVisibilitySystem; SocialMatchAuthority и SocialMatchSession — основные authority/visibility/work/match paths; MatchSession — startup/approval/heartbeat/resume/observer paths; NetworkSocialMatch — owner command/private delivery paths.
- Codex adapter — current start/thread/decision boundary; полная provider internals/auth/policy/stream mechanics не повторялись и не исследовались как OpenAI product support task.
- Инвентаризированы runtime packages/entry points, all relevant docs/source paths. Generated bindings, third-party DLL/license bulk, Unity caches/build outputs, historical evidence JSON, art binaries, редакторские world builders и полный тестовый корпус не заявляются как прочитанные этим агентом. Broadcast/MiniLM/art/deployment coverage распределено другим исследователям.

Весь отчет основан на конкретных исходниках и documentation inspection; любые дополнительные presentation claims должны иметь отдельное подтверждение соответствующего исследователя.
