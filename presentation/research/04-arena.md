# Дополнение: Agents Arena, физика, сценарии и представление

Исследовано 2026-10-23 только чтением в `/Users/fpetrov/PycharmProjects/Actor`. Отчет создан вне репозитория. Никаких Unity/build/test/model/OBS запусков, git fetch/pull и изменений Actor не выполнялось.

Все пути ниже относительно `/Users/fpetrov/PycharmProjects/Actor/`. Номера строк относятся к локальной копии во время исследования. Основной отчет по broadcaster: `/private/tmp/actor-broadcast-research.md`.

## Предметная карта

Agents Arena содержит два разных мира правил:

| Режим | Назначение | Основные сценарии | Authority |
| --- | --- | --- | --- |
| Sandbox | Изолированная проверка движения, навыков и кооперации | Навигация Hall/Engineering, station signals, совместная калибровка, расследование охлаждения | `CooperativeAuthority`, `IncidentAuthority`, station-signal rules |
| SocialDeduction | Общая среда людей и агентов со скрытой информацией | 4–6 участников, 1 Impostor, tasks, видимость, убийство, reporting, meetings, voting, life-support emergency, GameOver | `SocialMatchAuthority` |

Это режим комнаты, фиксируемый на создании. SocialDeduction не переиспользует открытый Sandbox duty roster, station-wide signal обход или глобальный peer roster. Одно и то же визуальное имя Engineer/Sheriff имеет разную политику раскрытия: в Sandbox duty публична и выбирается, в Social hidden duty назначается сервером независимо от hidden alignment.

`SocialMatchAuthority` — pure C# rule machine без Unity physics API и transport. `SocialMatchSession` — Unity integration: берет approved connection identities, фактические capsule centers, скорости и room membership, передает их authority, отправляет resulting deliveries точным получателям и синхронизирует body NetworkObjects. `NetworkSocialMatch` — owner endpoint, а не глобальная таблица чужих ролей. `AgentsArenaSocialBindings` переводит разрешенные локальные typed projections и intents в ACTOR capability registry.

Якоря: `Agents Arena/Assets/Scripts/Networking/SocialMatchAuthority.cs:11`, `SocialMatchAuthority.cs:114`; `Agents Arena/Assets/Scripts/Networking/SocialMatchSession.cs:14`, `SocialMatchSession.cs:158`; `Agents Arena/Assets/Scripts/Networking/NetworkSocialMatch.cs:10`; `Agents Arena/Assets/Scripts/Actor/AgentsArenaSocialBindings.cs:25`; `docs/roles.md:51`.

## Social rules: что действительно решает сервер

Начало матча требует от 4 до 6 connected living eligible participants, готовности каждого, fresh finite poses, доступности настоящих world terminals и достаточного числа Crew task instances. Один Impostor выбирается отдельным случайным перемешиванием; duty assignment использует независимое перемешивание. В production используется `RandomNumberGenerator`; deterministic fixture seed существует только как trusted server configuration.

Фазы: WaitingForPlayers -> RoleAssignment -> Exploration -> Discussion -> Voting -> VoteResolution -> Exploration, либо GameOver. Каждый phase transition меняет revision. Commands проверяют match, connection generation, correlation, phase revision, короткий expiry, bounded request shape, rate/capacity и жизнь/соединение отправителя. Generation проверяется до command replay lookup: старый bearer/action не может повторить ранее accepted command после reconnect. Replay key содержит actor+correlation и SHA256 тип+protobuf bytes; конфликт типов/содержимого не повторяет действие.

Важные defaults из текущего кода, а не product promises: minimum 4 / maximum 6 / Impostor 1; match 15 минут; discussion 90 секунд; voting 60; kill cooldown 30; visibility radius 3.5; kill distance 0.9; task threshold 8; meeting limit 3; one emergency meeting per actor. Эти числа конфигурируемые и для минимальной презентации не обязательны.

Kill требует Exploration, живого Impostor, cooldown, отсутствия конфликтующей работы, разрешенного currently visible target и физического расстояния. Witness определяется до elimination и должен видеть и attacker, и victim в момент действия. Body не содержит killer field. Дальнему observer невидимое убийство не меняет публичную revision и не создает death notice. Victim получает cosmetic presentation без attacker identity.

Meeting не телепортирует персонажей. Сервер прекращает movement и work, публикует разрешенный rollcall с life, но без world poses/alignments. Sabotage budget приостанавливается на все meeting phases; общий match timer продолжает идти. Ballot окончательный, до resolution приватен; abstention не превращается в Skip. Ejection требует единственного живого лидера строго выше Skip; ties дают no ejection.

Победа Crew: устранены все Impostors либо достигнут task threshold. Победа Impostor: parity либо просроченная life-support emergency. Match timeout — draw. Terminal state необратим; team win и survived — отдельные поля. Чужие роли и ordered replay разрешены только в postgame summary.

Якоря: `Agents Arena/Assets/Scripts/Networking/SocialMatchAuthority.cs:16` (configuration), `:363` (command gate), `:406` (TryStart), `:423` (Kill), `:448` (Report), `:465` (BeginMeeting), `:479` (Vote), `:488` (ResolveVote), `:644` (victory), `:654` (Finish). Имена методов сверены чтением, при копировании ссылок предпочтительны полные пути и определения.

## Задания уже не ограничены ожиданием у терминала

Текущий `TaskWork` поддерживает три typed mechanics, одинаковые для Human и agent:

1. `MaintenanceHold`: непрерывное серверно проверенное stationary physical presence.
2. `CircuitCalibration`: три timed steps, каждый с own displayed choice и exact step revision; ранний или устаревший шаг отклоняется.
3. `SensorObservation`: второй sample закрыт до серверного not-before; затем нужно выбрать реальный trend между двумя samples.

В session room Engineering сопоставлен hold, Security — sensor, остальные task rooms — circuit calibration. Для работы проверяются настоящий terminal transform, room, visibility, range, fresh pose, low velocity, life и phase. Работа имеет bounded deadline и own work ID; действие step и завершение целой work request — разные события. Общий успех подтверждает accepted completed result исходной work correlation. Уход, control/transport loss, смерть, phase change или timeout прекращают lease. Fake Impostor work использует ту же visible activity representation, но никогда не увеличивает Crew progress.

Якоря: `Agents Arena/Assets/Scripts/Networking/SocialMatchAuthority.cs:166`, `:187`, `:499`, `:517`, `:568`, `:571`, `:574`; `Agents Arena/Assets/Scripts/Networking/SocialMatchSession.cs:191`; `docs/reconnect.md:17`.

## Приватность существует до Python

`SocialVisibilitySystem` меняет server NGO observer sets, а не только renderer.enabled. Prefab устанавливает `SpawnWithObservers=false` и visibility callback до первого spawn. Unknown policy/viewer, отсутствующий installed pre-network hook, exceptions и насыщенный raycast buffer дают deny. В PlayerLoop callback стоит непосредственно до pinned NGO NetworkPreUpdate: свежая физика отфильтрована до следующей network replication. Owner сохраняет свой endpoint после death/meeting, чтобы получить собственный terminal result; это не дает чужого spectator oracle.

LOS блокируют только authored `SocialVisibilityOccluder` colliders. Characters, bodies и interaction triggers не становятся случайными стенами. Remote dynamic objects NetworkHide удаляет с клиента; NetworkShow создает fresh spawn/transform. Targeted owner RPC независим от spatial observers, поэтому audience каждого private event определяется server rules отдельно.

`VisibleWorld` — complete replacement list, включая пустые списки. `NetworkSocialMatch.CaptureWorld()` через 0.5 секунды без свежей world delivery возвращает пустую spatial projection. Own state, own tasks/work, own ballot и role evidence не хранятся в Everyone NetworkVariables. Визуальный avatar хранит лишь identity/name/cosmetic/motion, разрешенные его observer set.

Якоря: `Agents Arena/Assets/Scripts/Networking/SocialVisibilitySystem.cs:71`, `:91`, `:130`, `:164`; `Agents Arena/Assets/Scripts/Networking/SocialVisibilityMember.cs`; `Agents Arena/Assets/Scripts/Networking/NetworkSocialMatch.cs:10`, `:37`, `:70`; `Agents Arena/Assets/Scripts/Networking/SocialMatchAuthority.cs:291`, `:302`; `docs/visibility.md:3`.

## Движение, input и prediction

`CharacterMotor2D` — Rigidbody2D motor: bounded movement vector, max speed 2.6, acceleration 22, deceleration 30; physics stepping берет input intent, не команды «установить позицию». Shared `StepVelocity()` используется и authority physics, и owner presentation prediction.

`NetworkCharacter` исполняет physics motor только на сервере. Owner input отправляется unreliable RPC с sequence; press/release/direction changes обходят обычный период 30 Hz. Сервер отбрасывает не finite input, старые sequence и слишком частые команды; stale input 0.25 секунды дает zero. LateUpdate отправляет own post-physics reconcile snapshots примерно 30 Hz, содержащие фактически примененную input sequence и длительность применения. Adapter читает coherent authoritative snapshot отдельно от cosmetic interpolated/predicted visual child.

`OwnerMovementPrediction` не пишет network root/Rigidbody2D. Он предсказывает только visual child и camera target, replay unacknowledged input после snapshot, ограничивает replay 0.5 секунд, останавливает prediction при snapshot timeout 0.5 секунды. Sweep проверяет статическую геометрию actual capsule casts; другие players остаются server simulated. Малые reconcile errors затухают, большие corrections/recovery snap. Никакого model call на каждый physics tick нет.

`ActorCharacter` связывает identity/room/input/motion state. Human keyboard и `AgentInputSource` реализуют один input interface. `CharacterAnimationView` получает `ICharacterMotionState`, сохраняет фазу walk при смене направления и отражает диагонали; animation не изменяет truth/motor. Room membership вычисляет сервер по physical capsule center, не по словам агента.

Якоря: `Agents Arena/Assets/Scripts/Characters/CharacterMotor2D.cs:6`, `:38`; `Agents Arena/Assets/Scripts/Networking/NetworkCharacter.cs:14`, `:24`, `:124`, `:150`, `:163`, `:202`; `Agents Arena/Assets/Scripts/Networking/OwnerMovementPrediction.cs:7`, `:12`, `:67`, `:123`; `Agents Arena/Assets/Scripts/Characters/ActorCharacter.cs:9`; `Agents Arena/Assets/Scripts/Characters/CharacterAnimationView.cs:25`; `Agents Arena/Assets/Scripts/Networking/NetworkRoomMembership.cs:12`.

## Authenticated reconnect: текущее поведение

После unexpected transport loss тот же клиентский процесс может использовать private in-memory resume credential. Имя игрока identity не доказывает. Admission protocol 4, domain-separated HMAC, match/actor/name/version/credential/generation/expiry binding, targeted owner credential delivery и bounded server registry защищают takeover/rotation. Explicit Leave и RuntimeUnavailable отзывают resume rights и forfeit.

Default grace 30 секунд: server-owned retained avatar стоит, не действует и не выполняет work, но остается видимым и уязвимым. Возврат не resurrects killed/ejected participant; сохраняет own alignment/duty/life и ballot, повышает generation, заменяет transport endpoint. Grace expiry необратимо forfeits. Для начала bridge нужны свежая own generation и matching own visible-world bundle.

`docs/reconnect.md` описывает реальный staging probe: UDP blackhole 30.09 секунды, same process/identity, higher generation, hydrated own bundles, отказ old-generation action и stopped motion; все 10 owned processes/groups остановлены. Два setup failures сохранены рядом с одним actual fault pass. Это не native GUI recovery, whole-match success или доказанный live ghost kill.

Якоря: `Agents Arena/Assets/Scripts/Networking/SocialMatchAuthority.cs:226`, `:270`, `:363`; `Agents Arena/Assets/Scripts/Networking/NetworkSocialMatch.cs:106`; `Agents Arena/Assets/Scripts/Networking/ResumeAdmissionRegistry.cs`; `Agents Arena/Assets/Scripts/Networking/MatchSession.cs`; `docs/reconnect.md:1`; `docs/release-validation/network/reconnect.json`.

## Sandbox cooperative calibration

Coordinator и Technician — временные task assignments для двух разных approved actors, не hidden alignments и не Engineer/Sheriff duties. Coordinator в Hall инспектирует private 128-bit one-time authorization; Technician в Engineering получает его через обычное direct message и подтверждает actual receipt. Только после разрешенной private transfer и ACK сервер допускает SubmitCalibration. Знание или угадывание строки без legit delivery не завершает задачу.

Public progress не содержит challenge и private task details. `CooperativeAuthority` отдельно фильтрует Progress(), StateFor(actor), message recipients, sensitive flags, task scope, TTL и replay. Channels Sandbox direct/station; Social переиспользует только communication machinery с дополнительными phase/LOS policies и direct/proximity/discussion. При Social filtered broadcast sequence recipient-local, чтобы gaps не раскрывали скрытый traffic.

Task default deadline 5 минут, three attempts, cooldown 2 секунды. Loss of participant/control, expiry и cancel закрывают unfinished work; завершенное не стирается disconnect. Правильный room ID не заменяет distance до supplied actual terminal. ClientReceived доказывает получение клиентом, не понимание Brain.

Якоря: `Agents Arena/Assets/Scripts/Networking/CooperativeAuthority.cs:13`, `:113`, `:141`, `:183`, `:225`, `:241`, `:320`; `Agents Arena/Assets/Scripts/Networking/CooperativeSession.cs:14`; `Agents Arena/Assets/Scripts/Networking/NetworkCooperation.cs:14`; `docs/cooperative-tasks.md:9`.

## Sandbox расследование: почему нужна кооперация

Engineer владеет только technical diagnostic slice; Sheriff — bounded audit/security slice. Ни один не получает actual_cause enum. `IncidentAuthority` хранит hidden mechanical/manual origin и private active circuit только на сервере. Engineer ремонтирует; Sheriff подтверждает report. Разрешение задачи требует обеих независимых веток: physical cooling Restored И validated report. Distinct fixed identities и role lock не позволяют одному actor переключиться между ветками.

Сильный hard negative: accepted standby adjustment может быть безвредным рядом с mechanical failure; active adjustment создает manual fault. Оба origin имеют одинаковую pre-alarm acceptance surface и общий controller-cycle deadline, чтобы время alarm само не выдавало ответ. Engineer diagnostic задает circuit, bounded uncertainty window, wear/controller context и samples; Sheriff audit содержит bounded complete covered interval/circuits, реальные action/actor и neutral/nonqualifying records. Matching circuit string недостаточен: action, time overlap, completeness и controller revisions должны совпасть.

Sheriff report ссылается ровно на три разных вида identity: issued diagnostic bundle, issued security bundle и конкретный MaintenanceRecord. Сервер требует actual authorized cross-role disclosure, delivery ACK, lifetime, fixed roles, current incident/match, actual terminal proximity и semantic joint correlation. Три unsupported conclusive reports исчерпывают attempts; `InsufficientEvidence` — явный unresolved outcome, не successful classification. Wrong report не раскрывает correct origin.

Private evidence оформлена provenance match/incident/source/recipient/role/time/expiry/revision. Authorized model может использовать source как external data в session, но raw bundles и messages не становятся автоматически durable memory. Сообщение с цитатой Engineer остается external claim; server disclosure ledger может подтвердить доставку ссылки, но не превращает чужое сообщение в самостоятельное наблюдение Sheriff.

Якоря: `Agents Arena/Assets/Scripts/Networking/IncidentAuthority.cs:11`, `:90`, `:102`, `:139`, `:186`, `:317`, `:353`, `:369`, `:403`, `:419`, `:463`; `Agents Arena/Assets/Scripts/Networking/IncidentSession.cs:17`, `:134`, `:145`; `Agents Arena/Assets/Scripts/Actor/AgentsArenaInvestigationBindings.cs:23`; `docs/investigation.md:1`; `docs/incidents.md:13`; `docs/evidence.md:1`.

## Геометрия и public authored navigation

Sandbox использует Hall/Engineering, Social — отдельный четырехкомнатный clone с Hall/Engineering/Security/Storage и east-corridor loop. Room identities устойчивы, геометрия авторская. Capsule size 0.28x0.81 и root->center offset (0,0.46) принципиальны: root navigation node не равен interaction point physical center.

`SocialWorldBuilder` строит separate saved Social scene, actual colliders, turn landings, graph nodes/edges и terminal approaches, затем валидирует capsule overlap/sweep вперед/назад с боковыми offsets. ExportLayout() сериализует map из той же geometry. Unity `Assets/World/SocialLayout.json` и Runtime `actor_agents_arena/social_layout.json` представляют public static map knowledge, не dynamic actors. Navigation следует bounded authored graph и подтверждает arrival own fresh motion+room transition; при blockage возможен bounded backtrack/alternate route, а не универсальный dynamic pathfinder.

Sandbox старые passage tests и Social geometry tests — разные наборы. В docs/visibility.md есть исторический результат 195 actual geometry checks; нельзя объявлять новые geometry/GPU результаты только по наличию Editor validator.

Якоря: `Agents Arena/Assets/Editor/SocialWorldBuilder.cs:14`, `:242`, `:254`, `:270`; `Agents Arena/Assets/Scripts/World/SocialWorldDefinition.cs:7`; `docs/game-world.md:147`; `docs/visibility.md:51`.

## Представление и art: отдельный слой

Выбранная production direction — Atmospheric Minimalism: hooded characters, muted sage/clay/lilac/gold identity colors, quiet original station surfaces и dark glass UI. `AtmosphericArtCatalog` содержит только presentation refs; `AtmosphericPresentationHost` добавляет client-only components разрешенным spawned objects и не меняет NetworkBehaviour layout. `SocialAvatarArt` использует public stable cosmetic index, не alignment/duty/Human-agent status. Corpse — отдельный folded drawing, а не повернутый idle sprite.

`SocialKillPresentation` проигрывает bounded 640 ms fold только по разрешенному server event, exact match/recipient/stable presentation ID и freshness; spawn тела сам по себе не воспроизводит убийство. `PresentationLedger` bounded 256 identities блокирует repeated events. Sound/cosmetics прекращаются при потере разрешенной visibility. Victim effect может показывать собственное падение, но victim не получает attacker identity.

Fog не является privacy authority: это presentation поверх уже server-filtered observer policy. `SocialFogRenderer` имеет 192 angular sectors / 193 current-eye sight rays, opaque outside seal и inward feather. Закрытие clamp немедленное; открытие 75 ms bounded filter всегда <= нового allowed distance. Epoch/feed revocation убирает старую hole. Нельзя переносить старый LOS polygon к новому eye. Для Human moving/opening refresh следует rendered eye; idle и broadcaster — scheduled 30 Hz. `FogRefreshRules` отвечает только за timing; каждая outward update все равно требует свежего LOS.

`StationAtmosphere2D` добавляет public scenery backdrop и 10 fixed light pools, без новой authority/light visibility rule. Life-support emergency визуально меняет palette, а не вводит электрический blackout или меняет permitted sight radius. Scope includes match/connection/view epoch/audience: stale emergency envelope не переносится между матчами. `StaticSurfaceArt` отдельно проверяет видимый front/segment/volume bounds immutable landmarks, чтобы декоративная стена над fog не раскрывала лишнюю поверхность.

Один native Human A/B pair в `docs/fog-motion.md` показывает presentation-eye lag p99 7.1745 -> 0 px, но CPU fog p95 0.4994 -> 0.6524 ms и query rate x2.473. Это один same-binary pair с разными physical trajectories, idle OBS и без TTS. 119.8 callbacks/s не равно GPU/display FPS; нет whole-load performance PASS. Полезно для инженерных notes, не для рекламного числа «60/120 FPS».

Якоря: `Agents Arena/Assets/Scripts/Presentation/AtmosphericArtCatalog.cs:6`; `AtmosphericPresentationHost.cs:8`; `SocialAvatarArt.cs:7`; `SocialBodyArt.cs:8`; `SocialKillPresentation.cs:15`; `PresentationLedger.cs:8`; `PresentationSight.cs:7`; `StaticSurfaceArt.cs:8`; `TemporalFogRules.cs:5`; `FogRefreshRules.cs:6`; `StationAtmosphereRules.cs:7`; `Agents Arena/Assets/Scripts/World/SocialFogRenderer.cs:13`, `:102`; `docs/art-direction/style-guide.md:1`; `docs/atmosphere.md:5`; `docs/fog-motion.md:31`.

## Важные pure tests: что они доказывают

Выборочно прочитаны assertions, а не только имена:

- `tests/Actor.Sdk.Tests/SocialMatchAuthorityTests.cs:141`: spatial/public descriptors не имеют чужих alignment/duty/life/global kills; `:151` missing LOS fails closed; `:177` невидимое убийство не меняет публичные/дальние own bytes; `:189` witness видит обоих участников.
- Тот же suite `:609`: waiting roster становится explicit stable empty; `:625`: три timed exact revision calibration steps и original correlation completion; `:647`: второй sample реально закрыт до времени; `:666`: interactive timeout/cancel и fake work не дают Crew progress.
- Тот же suite `:682`: retained transport-lost avatar виден/уязвим, resume не resurrects victim; `:697`: ballot сохранен, grace expiry irreversible; `:710`: victim не получает attacker; `:724`: old generation не может replay accepted command.
- `tests/Actor.Sdk.Tests/CooperativeAuthorityTests.cs:73`: тот же правильный code отвергается до передачи и до нужного ACK, затем accepted; observer не получает private payload; `:144`: recipient-local sequences не раскрывают filtered traffic.
- `tests/Actor.Sdk.Tests/IncidentAuthorityTests.cs:137`, `:150`: paired views с одинаковым marginal одной роли и противоположными origins; joint evidence различает. `:160`: wear/controller metadata не constrains private circuit. Также suites проверяют fixed roles, exactly three refs, both branches, timing/terminal loss, replay/cross-match.
- `ResumeAdmissionRegistryTests`, `PresentationLedgerTests`, `FogRefreshTests`, `StaticSurfaceRulesTests`, `StationAtmosphereTests`: signatures/rotation/bounds; event replay/freshness; current-eye timing; immutable surface visibility; scope/feather/emergency bounds.

Эти тесты pure/auth-free, не доказывают general social intelligence, model naturalness, GUI readability, native smoothness, GPU cost или end-to-end privacy всех новых builds. Никакие тесты в этом исследовании не запускались.

## Предупреждения об устаревших документах

1. `docs/social-deduction.md:3` описывает introduction Game 0.8, `:12` говорит admission protocol 3, а `:116` утверждает отсутствие authenticated resumable identity. Это исторический MVP. Текущий protocol 4/reconnect/task mechanics описаны в `docs/reconnect.md` и явно реализованы в коде. Не копировать отсутствие reconnect в текущую презентацию.
2. `docs/game-world.md:3` говорит Game 0.8.0; геометрические принципы остаются полезными, но version stamp не current release declaration.
3. `docs/cooperative-tasks.md:49` фиксирует исходное появление calibration extension 0.3.0 / Game 0.6.0. `docs/incidents.md:86` фиксирует investigation Game 0.7.0 / Runtime 0.5.0. `docs/roles.md:16` также содержит прежнюю extension version. Это не текущие package versions; смотреть manifests/README/generator source.
4. Old duty roster относится к Sandbox. Его публичность нельзя переносить на live Social duty/alignment.
5. Editor validation code, synthetic screenshots и headless clients не доказывают native rendering. Исторический count tests/builds и native checkpoint сохраняет свой source identity; не переносить его на текущий commit без нового запуска.
6. Source observer cadence, networking ticks, Human fog callbacks и OBS FPS — разные величины. Actual four-player BF16/OBS acceptance имеет собственный ресурсный FAIL, описанный в основном broadcast report.

## Охват дополнительного чтения

Инвентаризированы все группы `Assets/Scripts` (96 C# файлов, включая generated types) и `Assets/Editor` (16 C# files). Generated protobuf classes и .meta не анализировались построчно.

Подробно прочитаны principal behavior/authority branches `SocialMatchAuthority`, `SocialMatchSession`, `NetworkCharacter`, `OwnerMovementPrediction`, `CharacterMotor2D`, `ActorCharacter`, `CharacterAnimationView`, `SocialVisibilitySystem`, `NetworkSocialMatch`, `CooperativeAuthority`, `IncidentAuthority`; у больших authorities особенно command/permission/projection/deadline/work/terminal branches. Некоторые длинные output fragments были усечены интерфейсом чтения; отчет сознательно не заявляет построчное чтение каждой ветки.

Целиком или близко к полному прочитаны компактные presentation modules: `AtmosphericArtCatalog`, `AtmosphericPresentationHost`, `SocialAvatarArt`, `SocialBodyArt`, `PresentationSight`, `PresentationLedger`, `TemporalFogRules`, `StationAtmosphereRules`, `StaticSurfaceArt`, `FogRefreshRules`; principal event and visibility paths `SocialKillPresentation`; structure/refresh paths `SocialFogRenderer`.

По interfaces/registration/public entrypoints просмотрены `AgentsArenaGameAdapter`, `AgentsArenaSocialBindings`, `AgentsArenaInvestigationBindings`, `IncidentSession`, `CooperativeSession`, `NetworkInvestigation`, `NetworkCooperation`, `NetworkRoomMembership`, `SocialWorldDefinition`, Editor `SocialWorldBuilder`, world/art/import/build/verification entrypoint inventory.

Полностью по смысловым разделам прочитаны `game-world.md`, `investigation.md`, `incidents.md`, `cooperative-tasks.md`, `roles.md`, `evidence.md`, `social-deduction.md`, `visibility.md`, `voting.md`, `reconnect.md`, `atmosphere.md`, `fog-motion.md`, art production style guide; networking architecture просмотрена вместе с источниками. Test suites проиндексированы, ключевые assertions по privacy/three mechanics/resume/no-solo/paired evidence прочитаны непосредственно.

Не заявляется повторная проверка каждого UI controller, editor art export, source PNG/license asset, shader branch, legacy diagnostic probe или каждого validation JSON. Эти группы перечислены в общем source inventory и требуют отдельной задачи, если цель меняется с архитектурного исследования на полный аудит.
