# ACTOR: дополнение к исследованию Core, установки, контрактов и поставки

Дата: 23 октября 2026 года. Продолжение `/private/tmp/actor-core-research.md`.
Исходники: `/Users/fpetrov/PycharmProjects/Actor`. Все действия с Actor были только чтением. Скрипты не запускались; сетевые сервисы, CI, сборки и файлы Actor не изменялись. Само наличие guard/test кода не считается новой приемкой.

## 1. Новое важное понимание

Проект проектирует не только цикл агента, но и цепочку доверия от схемы и исходного коммита до установленного wheel, нативного клиента и согласованного серверного релиза. Разделены четыре вещи: **build**, **package**, **publish**, **deploy**. Ни одна не объявляется доказательством игрового результата.

Generic SDK и admission contracts действительно изолированы от Unity engine: обе assembly definition имеют `noEngineReferences: true`. SDK содержит только `Google.Protobuf.dll` как precompiled reference, concrete gRPC находится в другом пакете. Shared admission contracts имеют отдельную версию UPM0.1.0; ее не нужно путать с ACTOR Core1.0 или admission protocol4.

Источник: `packages/com.actor.sdk/Runtime/Actor.Sdk.asmdef`; `shared/com.actor.contracts/Runtime/Actor.Contracts.asmdef`; package.json соответствующих пакетов.

Локальны Python Runtime, Brain adapter, SDK process и TTS worker. **Модель Codex вызывается как внешний provider**, поэтому фраза «модель работает локально» неверна для Codex. Детерминированный Brain и MiniLM classifier могут исполняться локально. VPS содержит reference game server, Control Plane и static web, а не агентный gRPC/LLM сервис.

## 2. Установленный продукт: source checkout необязателен

`docs/agent-onboarding.md` и `docs/installation.md` описывают runtime wheel + matching native client как самостоятельный путь. Человек запускает игру без Python, SDK/model account. Агент дополнительно устанавливает wheel в Python3.12+ и provider extra по выбору.

`actorctl doctor` проверяет installed package versions, entry points и client manifest без login/model calls. `actorctl codex status/login` использует официальный SDK flow, не читает credential files. `actorctl join` сам выбирает loopback port, создает fresh environment-only credential, запускает Runtime и обычный game client. Нет автоскачивания клиента, автоматического выбора hidden alignment или подмены real provider на scripted после failure.

Важное различие defaults: **installed `actorctl join` использует `--memory none` по умолчанию**, а явное `--memory sqlite` дает session-isolated in-memory SQLite, если одновременно не заданы durable path+profile. Development Social supervisor специально создает отдельный persistent DB/profile каждому участнику. Поэтому «у каждого агента обязательно файл SQLite» — слишком сильная общая формулировка.

Установленный client manifest связывает game_id/game_version с executable path и SHA-256. Path должен оставаться внутри release directory, outward symlink/traversal отклоняется. Без manifest/checksum явно выбранный development binary помечается unverified. SHA проверяет целостность относительно доверенного manifest, не аутентифицирует неизвестный источник.

Saved ownership record позволяет `status/stop` сравнивать PID, OS start time и command fingerprint. Нет `killall` по общему имени процесса. `stop_requested` — только подтверждение запроса; конечный статус и cleanup_complete проверяются отдельно. Guarded cross-process stop заявлен для Unix, не для Windows.

Основания: `docs/agent-onboarding.md`; `docs/installation.md`; полностью прочитанные ранее `runtime/actor_runtime/control.py`, `launching.py`.

## 3. Три разные admission credential domains

### Player join

`JoinTicketCodec` кодирует UTF-8 JSON в base64url и подписывает encoded payload через HMAC-SHA256. Verify ограничивает ticket1024 символами, требует secret минимум32 байта, проверяет подпись без раннего выхода по отдельным байтам, строго декодирует UTF-8. Claims включают lobby, name, game/admission version, expiry, nonce и protocol4 actor/generation/resume fields.

`ClaimsMatch` допускает expiry максимум на300с вперед. Имя —2–24 Unicode letters/digits и разрешенные пробел/подчеркивание/точка/дефис, без edge whitespace. При обычном protocol4 join generation==1; resume имеет собственные claims constraints.

Источники: `shared/com.actor.contracts/Runtime/JoinTicketCodec.cs:25`, `JoinTicketCodec.cs:34`, `JoinTicketCodec.cs:58`, `JoinTicketCodec.cs:67`.

### Resume

`ResumeCredentialCodec` использует отдельный HMAC-derived domain `ACTOR:resume:v1` и wire prefix `ar1.`. Нельзя принять resume bearer как player join ticket, даже при общем match key. Claims связывают match, actor, name, versions, credential ID, generation и expiry. Secret строго32 байта, payload<=700 UTF-8 bytes, public credential bound<=2048 chars; claims lifetime<=180с.

Это не durable identity/account system: credential остается в памяти того же клиента. Неожиданная transport loss допускает попытку resume, Explicit Leave и Runtime unavailable отзывают права. Default30с grace не равен гарантированному reconnect window. Retained avatar останавливается, но остается наблюдаемым/уязвимым; resume не воскрешает. Generation и rotating ACK-bound credential защищают от старых действий/takeover.

Источники: `ResumeCredentialCodec.cs:19`, `ResumeCredentialCodec.cs:23`, `ResumeCredentialCodec.cs:38`; `docs/reconnect.md` (прочитан в QA).

### Broadcast observer

`ObserverTicketCodec` использует audience `actor.agents-arena.broadcast-observer.v1`, derived signing key и wire prefix `observer-v1.`. Lifetime<=60с, generation1..1_000_000, отдельные broadcastSessionId/observerId. Derived key очищается после подписи/проверки. Наблюдатель не использует player/resume domain; не становится игроком и не занимает player slot.

Источники: `ObserverTicketCodec.cs:24`, `ObserverTicketCodec.cs:31`, `ObserverTicketCodec.cs:52`.

Типизация и HMAC защищают identity/binding/replay admission, но не шифруют HTTP/UDP игровой транспорт.

## 4. Sandbox как намеренно асимметричная проверка кооперации

В проекте три независимых пространства назначений:

1. Temporary **Coordinator/Technician** в calibration task.
2. Public Sandbox duties **Crew/Engineer/Sheriff**.
3. Hidden SocialAlignment **Crew/Impostor**, независимо от station duty.

Их нельзя свести к одному списку игровых ролей. В Social side/duty назначаются сервером; Sandbox допускает request public duty, calibration привязывает двух участников отдельного task.

### Calibration

Coordinator физически подходит к Main Hall Diagnostic Terminal, получает только ему адресованную короткоживущую authorization. Он должен переслать ее Technician через обычное sensitive direct message. Technician физически приходит к Engineering Console; сервер требует текущую assignment, правильный code, expiry/attempt budget и **actual receiving-client ACK** законной передачи.

Public progress имеет отдельную схему и не содержит code/private assignment evidence. Верный guessed code без признанной доставки не завершает task. Client/server/control loss отменяет unfinished task, но не стирает completed. Replay возвращает исход, не повторяет action/delivery. Skill arrival/bridge acceptance/server message receipt по отдельности не доказывают calibration completion.

Источники: `docs/cooperative-tasks.md`; `docs/communication.md` (основной отчет).

### Investigation

Engineer видит bounded technical diagnostic; Sheriff — собственный audit slice. Никому не выдается `actual_cause`. Evidence имеет server-issued GUID/provenance, match/incident, issuer/source, recipient duty, revision и expiry. Public cooling status не имеет private evidence/hidden origin.

Security evidence явно задает covered interval/circuits и completeness; отсутствие записи осмысленно только в этой области. В reference controller cycle допускается максимум одно реальное adjustment на circuit. Временные окна coarsened до завершенного общего cycle, чтобы публичный timestamp не был answer oracle. Natural fault и manual active-circuit fault сигнализируются на общей deadline; standby action может быть безвредным hard negative. Default secret circuit выбирается независимо от public match ID/controller revisions/wear.

Report требует точно diagnostic bundle GUID, собственный security bundle GUID и correlated MaintenanceRecord ID, а не только совпавшую строку circuit. Foreign diagnostic reference должен реально дойти до Sheriff по разрешенному ACK-подтвержденному direct path. Это разрешает citation, но не превращает player prose в authoritative чужой own observation.

Physical cooling repair и validated investigation report — две независимые ветки. Только обе дают `Resolved`. Инцидент фиксирует разных Engineer/Sheriff и lock role switch, поэтому один actor не может выполнить обе ветки. Ответ «InsufficientEvidence» допускает неопределенность, но не считается successful conclusive classification. Неверный report не выдает правильную cause.

Источники: `docs/roles.md`, `docs/evidence.md`, `docs/incidents.md`, `docs/investigation.md`.

Эти foundational guides частично сохраняют версии M5/M6/M7. Их versions не являются current package versions; весь Sandbox функционал сохранен в current game0.10.

## 5. Геометрия — статическое знание, а не hidden-world feed

`docs/game-world.md` объясняет две отдельные multiplayer scenes. Sandbox оставляет Main Hall/Engineering и специализированные terminals; Social добавляет Security/Storage/east corridor. Room IDs принадлежат игре. Server room membership использует capsule center, route waypoint — actor root. Offset `(0,0.46)` нельзя игнорировать в объяснении range/nav.

SocialLayout.json экспортируется из authored Unity geometry и включается в установленный Arena Runtime package. Он содержит floor polygons, capsule shape, root nodes/edges и terminal approaches; не содержит live positions/roles. Social navigation использует bounded alternate route/backtracking, если достигнутое ребро блокируется; unreachable start/final approach завершает явной ошибкой. Это reference-game route solver, не универсальная dynamic-obstacle навигация для любой игры.

Документ отдельно фиксирует human capsule passage defects: прямолинейный бот мог завершать centerline route, хотя человек застревал при боковом выходе. Поэтому actual physics/capsule sweeps и native Human evidence выделены в отдельные проверки.

`docs/station-signals.md` описывает маленький training Signal Console: routine/urgent enum без произвольного текста, caller/room/proximity/cooldown validation, server-generated IDs/revision, explicit station-wide audience. Нет публичного HTTP event generation API. Training signal не является общим chat и не должен давать private bypass в Social.

## 6. Development launch supervisors: process ownership и completion proof

Полностью прочитаны development launchers. Их JSON/log files — diagnostics/acceptance metadata, не action/observation IPC. Код supervisor не передает соседу game payloads.

| Скрипт | Назначение и важные границы |
|---|---|
| `launch-scripted-agent.py` | Самый ранний deterministic runtime example; fresh env token; ordinary client; удерживает client после scripted completion; owned child cleanup |
| `launch-agent.py` | Общий development launcher agent/match; проверяет port, objective, API URL; генерирует token; ждет runtime socket; запускает client800x600/headless по выбору; записывает metadata с token fingerprint, не raw token |
| `launch-cooperative-agents.py` | Два независимых coordinator/technician процесса; стартует Technician первым и ждет только readiness; matching Completed public proof/session/actor barrier перед cleanup |
| `launch-investigation-agents.py` | Два Engineer/Sheriff; отдельные memory paths/profiles/ports; matching incident Resolved+Restored+validated+own Objective success; лачит terminal proof до сброса view при shutdown |
| `launch-social-agents.py` |4–6 isolated role-agnostic participants, один общий trusted objective; проверяет distinct PIDs/ports/credentials/databases/profiles и совпадающий authoritative GameOver/summary/winner/reason |

Social supervisor default headless, `--windowed` — отдельный visual path. Имена выбираются OS randomness, не game RNG; `--brains` выбирает provider, не affiliation. Параметр `--players` должен совпадать с server MinPlayers, не меняет room rules. Каждый launcher создает свою process group; teardown сначала позволяет Runtime/Skill cleanup, затем эскалирует только группы, которыми владеет.

Основания: `scripts/launch-social-agents.py:112`, `:142`, `:158`, `:200`, `:334`; остальные launchers прочитаны полностью.

Supervisor completion доказывает корректное участие/совпадение результата, не то, что все участники выиграли или что модель социально рассуждала. Controlled `social-fixture-*` — mechanics providers, не live intelligence result.

## 7. Генерация и зависимости

`generate-proto.sh` создает pinned venv в ignored build. Python generator скачивает только pinned official protoc/grpc assets с известными SHA, генерирует message bindings и official stream stub, descriptor sets с imports, helpers и package metadata.

Auto discovery читает `proto/**/extension.json`; extra manifest допускается явно. Path validator запрещает absolute/traversal/Windows drive/outward symlink, требует dedicated C# Generated directory, проверяет duplicate IDs/artifacts/resources/source ownership и filename collisions. Новый package не требует game-ID ветки в Core generator.

Fingerprint: stable proto path order, source_code_info removed, deterministic descriptor serialization, SHA256 exact bytes. Это integrity/compatibility identity, не semantic compatibility theorem. `.meta`/cache files исключены из freshness comparison. `--check` генерирует temporary output и сравнивает committed artifacts; обычный generate перезаписывает managed outputs. Поэтому в этой read-only задаче ни один вариант не запускался: даже check создает ignored build/cache.

Self-test создает fictitious Mana extension в isolated fixture tree, проверяет generated Any roundtrip, helper/hash, automatic/explicit discovery и path guards. Наличие test не равно его исполнению сегодня.

Источники: `scripts/generate-proto.py:51`, `:65`, `:166`, `:281`, `:356`.

`fetch-unity-grpc-dependencies.py` восстанавливает locked NuGet dependencies, выбирает ровно Unity-compatible runtime DLL, проверяет NuGet SHA512, копирует license notices и сохраняет assembly SHA256 inventory. Google.Protobuf идет в SDK, Grpc/client/support libraries — в transport package; framework-provided Memory/Buffers/Tasks не дублируются.

`docs/grpc-spike/README.md` сохраняет исторический стек выбора: normal Mono HTTP handler не считался доказанным HTTP2 транспортом, выбран native YetAnotherHttpHandler. Local duplex compatibility проверялась отдельно до Core implementation. IL2CPP и Windows/Linux client streaming не доказаны этой macOS исторической проверкой. Это ограничения platform acceptance, а не generic contract.

## 8. Реальный wheel проверяется вне checkout

`verify-runtime-package.py` не ограничивается editable install. Он копирует runtime/generated source в изолированный snapshot, строит wheel, создает fresh venv, убирает model credential keys/PYTHONPATH/PYTHONHOME, устанавливает wheel+base/dev/broadcast deps и запускает import/resource/plugin probe из outside-checkout cwd.

Probe требует, чтобы module paths находились внутри venv; Codex/Jev/MLX extras отсутствуют. Проверяет generated descriptor resources, package versions, entry points, auth-free actorctl doctor, privacy policy и broadcast resource files. Затем копирует только fixtures и выполняет полный Runtime test suite против installed wheel. Это проверяет packaging/import boundaries без модели или live multiplayer.

Report сохраняет stage/kind/return code/test counters, не сырые pip outputs/assertion fixtures/private traceback payloads. Export wheel выполняется только после успеха, сверяет exact SHA и не заменяет отличающийся существующий artifact. Model-less base install остается работоспособным, даже если optional adapters могут импортироваться lazily.

Источники: `scripts/verify-runtime-package.py:40`, `:89`, `:182`, `:289`.

## 9. Release packaging и provenance

`build-provenance.py capture` включает tracked и nonignored untracked inventory, mode, symlink target, file digest, HEAD и dirty status. До/после snapshot проверяется неизменность inventory/status/HEAD; source file lstat проверяется на изменение во время hash. Control Plane stamp требует точно тот же snapshot после test/publish, Release/linux-x64, matching appsettings0.10/admission4 и реальные executable/DLL SHA.

Источник: `scripts/build-provenance.py:36`, `:83`.

`package-release.py` **не собирает, не публикует, не устанавливает и не подписывает** продукт. Он требует имеющиеся native macOS client / Linux server / Linux CP stamps на одном clean commit, корректные native headers/modes и exact runtime wheel payload. Dirty staging только explicit `--allow-dirty`, всегда publishable_source=false.

Wheel validation сравнивает все package files с текущим source, metadata Python/dependencies/entry points/RECORD checksums и rejects extra/missing files, duplicate/unsafe paths, symlinks. Native archives проверяются по exact membership, SHA/size, execute bits и contained relative symlinks, потом реально извлекаются и сравниваются повторно (macOS ditto).

Получается пять продуктов плюс release-manifest.json и SHA256SUMS: семь файлов, в checksum index шесть entries. Products — macOS client, Linux dedicated server, Linux Control Plane, Python wheel, отдельный onboarding Skill zip. Последний ограничен двумя authored files. Нет Windows artifact, Apple notarization, обещания публичного Linux player или доказательства deterministic Unity compiler.

Источники: `scripts/package-release.py:137`, `:178`, `:289`, `:362`, `:377`.

## 10. Deploy — управляемое окно, не бесшовная миграция

Current deployment docs описывают Ubuntu24.04 self-contained CP под system user actor. nginx TCP80, Kestrel127.0.0.1:5080, gameplay UDP7777–7781. В current VPS нет Docker orchestration, Unity Editor или agent runtime. Existing SSH/firewall configuration provisioning не меняет.

`deploy-vps.sh` делает early zero-room read guard, загружает новые versioned CP/server roots с rsync copy-dest без shared mutable release inodes, web/config/scripts — в staging. Он сам не повторяет полный packaging source-coherence audit; изменившиеся build roots надо перепроверять.

Activation берет exclusive flock и owned drain marker, делает backup aliases/env/unit/nginx/full website, проверяет zero rooms, останавливает точный nginx service и доказывает его cgroup empty, повторяет serialized CP zero-room barrier, только после этого останавливает CP/game cgroup. Это закрывает race с запросом, который nginx принял до drain. Активные комнаты не перезапускаются ради deployment: операция отказывается при их наличии.

Затем меняются current symlinks CP/server, web content, env versions, daemon config. CP должен пройти health и exact game/admission check до возврата nginx. Две symlink changes не одна atomic filesystem transaction; nginx pause затрагивает shared HTTP ingress, uninterrupted availability не обещается. Успешный web rsync не удаляет лишние destination files, поэтому exact deployed inventory проверяется отдельно.

При неудаче activation пытается восстановить captured deployment; failed rollback оставляет ingress stopped/drained для явного recovery. Routine rollback после успешного deploy — отдельная gated maintenance операция, а не просто repoint alias под живыми матчами. DB/logs остаются.

Источники: `docs/deployment.md`; `scripts/deploy-activate.sh:60`, `:113`, `:122`, `:149`, `:180`; `scripts/deploy-vps.sh`; nginx/systemd configs.

Systemd uses KillMode=control-group, PrivateTmp, NoNewPrivileges, ProtectSystem=strict, ProtectHome и limited ReadWritePaths. CP restart намеренно завершает активные матчи, потому что signing keys не persist. Capacity validator создает реальные комнаты и шестую rejected allocation; **это не read-only health check**. В этой задаче он не запускался.

## 11. CI действительно разделяет классы проверки

`.github/workflows/actor-sdk.yml` содержит три jobs:

- **protocol-runtime**: generated freshness, .NET solution tests, base/dev/broadcast Python tests и helper/fixture guards; отдельно утверждает отсутствие optional openai_codex/typesafe SDK. Запускает auth-free fault/privacy/protocol/postgame/resume/release/art checks и curated event-routing evaluation.
- **broadcast-provider-policy**: устанавливает pinned official SDK и запускает local auth-free outgoing-tool-definition policy probe. Это не Twitch live test.
- **minilm-classifier**: отдельно устанавливает optional local MiniLM package и запускает real-weight/private recording/executable-plan integration tests. Это не autonomous win-rate experiment.

Control Plane workflow отдельно запускает solution .NET tests. Workflow files описывают intended CI steps; чтение yaml не подтверждает конкретный последний run. Root отдельно собирает current remote state, поэтому в presentation test pass следует ссылаться на receipt/run, не на наличие workflow.

Источники: `.github/workflows/actor-sdk.yml:29`, `:52`, `:65`; `.github/workflows/control-plane.yml`.

## 12. Дополнительный фактический охват

Не повторялись уже полностью разобранные runtime Core и hand-authored SDK classes из основного отчета. Дополнительно полностью прочитаны:

- Docs: `agent-onboarding.md`, `installation.md`, `cooperative-tasks.md`, `roles.md`, `evidence.md`, `incidents.md`, `investigation.md`, `game-world.md`, `station-signals.md`, `deployment.md`, `release-plan.md`, `grpc-spike/README.md`, SDK README; ранее во время site QA дополнительно `reconnect.md`, `visibility.md`, `social-reasoning.md`.
- Shared contracts: все три hand-authored codecs и claims (`JoinTicketCodec.cs`, `ResumeCredentialCodec.cs`, `ObserverTicketCodec.cs`); SDK/shared asmdef/package metadata.
- Build/release: common.sh, build-client.sh, build-server.sh, build-web.sh, publish-control-plane.sh, build-provenance.py, package-release.py, verify-runtime-package.py, generate-proto.sh/py, fetch-unity-grpc-dependencies.py.
- Deployment: deploy-vps.sh, deploy-activate.sh, provision-vps.sh, verify-capacity.py, nginx actor.conf и systemd unit.
- Development launch: launch-agent.py, launch-scripted-agent.py, launch-cooperative-agents.py, launch-investigation-agents.py, launch-social-agents.py.
- Оба GitHub workflows. Инвентаризированы функции summarizers/evaluate-events/compare-routing/latency-relay, но полный построчный разбор этих дополнительных analysis helpers **не заявляется**.

Не объявляются прочитанными этим subtask: полный historical validation JSON/markdown corpus, generated bindings побайтово, все unit tests, binary DLL/art, Unity cache/build outputs. Broadcast/MiniLM/UI/art проверяются другими исследователями. Все reports разделяют source-presence, deterministic tests, fixture/live multiplayer, native Human, provider и public Broadcast acceptance.
