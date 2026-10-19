# Milestone 6 — ролевое расследование Agents Arena

**Статус: Milestone 6 реализован и проверен через VPS.** Приняты шесть
расследований: два scripted, оба mixed-направления и два сценария с настоящими
независимыми CodexBrain. Оба происхождения и misleading claim проверены.
Ошибки разработки и границы проверок сохранены ниже.

Публичный отчёт содержит агрегаты и безопасные технические сведения. Здесь нет
текстов приватных сообщений, diagnostic payload, authorization codes, токенов,
учётных данных или внутреннего рассуждения модели. Сырые рабочие отчёты остаются
в игнорируемом `build/investigation-validation/`.

## 1. Реализованная архитектура

Игровая `IncidentAuthority` хранит роли, реальную историю действий, скрытый
контур, цикл контроллера, выдачу свидетельств, ремонт и проверку отчёта.
`IncidentSession` передаёт ей только одобренные сервером ActorId и физические
позиции, а `NetworkInvestigation` использует существующий owner RPC path.
Приватные результаты доставляются конкретному владельцу, публичный статус
существует отдельно.

```text
Brain → typed Runtime Skill → существующий gRPC bridge → Unity adapter
                                                       ↓ owner RPC
                                              Dedicated Server
                                           IncidentAuthority
                                        ↙                     ↘
                          приватный ответ владельцу      публичный статус
```

Engineer и Sheriff имеют независимые процессы Runtime, WorldState, Scheduler,
Brain/thread и SQLite. Общение идёт через обычный игровой сервер и Standard
communication. Supervisor не переносит улики между агентами. Core не содержит
веток Engineer/Sheriff или incident. Подробности: [архитектура](../investigation.md),
[граница протокола](../actor-protocol.md), [изоляция процессов](../multi-agent.md).

## 2. Новые роли

| Duty | Назначение |
| --- | --- |
| Crew | Обычный участник; публичный статус, сообщения, разрешённое обслуживание |
| Engineer | Собственная техническая диагностика, ремонт, разрешённое обслуживание |
| Sheriff | Собственный журнал безопасности и проверяемый итоговый отчёт |

Это публичные станционные duties, отдельные от временных Coordinator/Technician
в предыдущей calibration-задаче. По умолчанию участник — Crew. Engineer и Sheriff
уникальны и принадлежат разным ActorId. См. [роли](../roles.md).

## 3. Permissions и серверная проверка

Сервер получает инициатора из одобренного соединения и принадлежащего ему
NetworkObject. Клиент не передаёт произвольный sender ActorId. Проверяются
match/incident, duty, физический объект, комната, конечность координат,
расстояние до терминала, состояние, срок, correlation, размер, лимиты запросов
и корректность ссылок. Во время Active изменение роли запрещено.

Выбор duty доступен через HUD. Игровые работы требуют proximity: Cooling Control
имеет interaction point `(-0.25,-6.54)`, radius1.25; Security Records —
`(-1,-0.65)`, radius1.6. Расстояние считается от серверного центра капсулы.
UI и локальный preflight не заменяют эти проверки. См. [мир](../game-world.md).

## 4. Новые Protobuf schemas

Добавлен [incidents.proto](../../proto/extensions/agents_arena/v1/incidents.proto).
Он описывает own role/roster, публичный cooling state, lifecycle events,
role-private diagnostic/security evidence, provenance, собственный investigation
view и типизированные команды/результаты selection, inspection, repair,
maintenance, cancellation и report.

Существующие room, StationSignal, calibration, Core и Standard schemas сохранены.
Maintenance/report не маскируются под movement command или произвольный JSON RPC.

## 5. Версии и generated artifacts

| Компонент | Версия |
| --- | --- |
| Agents Arena game/client/server |0.7.0|
| Arena extension |0.4.0|
| Python Runtime |0.5.0|
| Core protocol |1.0|
| Standard motion |0.1.0|
| Standard communication |0.1.0|
| Multiplayer admission |3|

Arena descriptor SHA-256:
`cc40698a9135d457ea1ff4d24c64fc5a8f7edb28a8adcb67319f88f8ab268fb6`.
Обычный manifest-driven generator создал C#/Python bindings, descriptor set,
metadata/manifest и typed extension helper. Проверка свежести — **52/52**.
Состав установленного wheel и хеши расширений: [package.json](package.json).

## 6. Incident lifecycle

Cooling проходит `Operational → Failure → Repairing → Restored`.
Investigation проходит `None → Active → Resolved` либо завершается
`Cancelled`, `Expired` или `Failed`. `Resolved` требует одновременно
`CoolingStatus.Restored` и `ReportValidated=true`.

Стандартные сроки: общий цикл 15s, active incident 300s, repair lease 2s.
Ремонт проверяет присутствие/разрешение/терминал в течение операции.
CancelRepair освобождает lease; required participant/control loss отменяет
незавершённый incident. До сигнала потеря/смена required duty сбрасывает
подготовленный цикл. Завершённый Resolved сохраняется после disconnect.
Один lobby/process содержит один investigation. См. [lifecycle](../incidents.md).

## 7. Два происхождения аварии

`MechanicalFailure` возникает по независимому серверному расписанию.
`ManualInterference` требует реальной разрешённой maintenance-операции на
активном контуре. Работа с резервным контуром остаётся benign и записывается
с настоящим ActorId. Близость Crew или недавняя операция сами по себе не
доказывают причинность. Публичного поля origin/cause нет.

## 8. Правила hidden cause

Обычный скрытый selector использует независимую серверную криптографическую
энтропию. Его нельзя вычислить из public match ID, equipment wear или controller
revision. Trusted match-scoped server fixture может задать воспроизводимые
условия; публичной debug-команды выбора причины нет, Brain не получает config.

Обе готовые duties запускают общий цикл. Реальная active maintenance подготавливает
latent valve fault; обе причины сигнализируются на одном deadline после
завершения цикла. Это исключает отдельный немедленный manual-only alarm.
Не более одной принятой adjustment на каждый контур за цикл: полный bounded
audit не скрывает лишние qualifying writes. Раннее действие и позднее действие
получают правдивый общий uncertainty interval; future completeness не заявляется.

## 9. Diagnostic Evidence

Только назначенный Engineer у реального Cooling Control получает собственный
`DiagnosticEvidence`: provenance, circuit, завершённый uncertainty window,
controller revision before/after, equipment wear и шесть конечных pressure
samples при schema/policy bound≤8. Все sample timestamps не позднее inspection.
Одинаковые pressure/wear/revision patterns допустимы при обеих причинах;
`before != after` не используется как скрытый manual-флаг.

## 10. Security Evidence

Только назначенный Sheriff у Security Records получает свой `SecurityEvidence`.
Он содержит две primary записи по контурам и три neutral/nonqualifying записи,
при bound≤6. Запись включает RecordId/source, circuit, interval, action,
наблюдавшийся настоящий ActorId при наличии, confirmation и controller context.

Coverage явно задаёт завершённый интервал, два listed circuits и completeness.
Actual records coarsened до интервала, который действительно включает действие.
Neutral controller history не выдаётся за действие выдуманного игрока.
Qualifying write не отбрасывается ради соблюдения bound. См. [evidence](../evidence.md).

## 11. Evidence provenance

Сервер выдаёт непрозрачный GUID, связанный с incident, match, source entity,
recipient actor/duty, observed/expiry timestamps и revision. Report разрешает
ссылаться только на реально выданные текущие объекты, а не на произвольные
строки игрока. Own view содержит только собственный легитимно inspected bundle.
По окончании доступа сервер отправляет явный пустой view для очистки merged cache.

## 12. Privacy model

Private evidence не хранится в глобальном NetworkVariable/chat log. Delivery
проверяется сервером до targeted owner RPC; фильтрация Python не используется
для исправления публичной утечки. Идентификаторы текущих private bundles могут
передаваться лишь sensitive direct assigned partner в текущем incident.

Engineer quote остаётся player claim с server-identified sender. Получение не
создаёт у Sheriff чужую authoritative own observation. Private working data
разрешены авторизованному Brain в текущем эпизоде; SQLite и публичные diagnostics
получают безопасную проекцию. Это visibility model, не обещание шифрования от
сервера или защиты от авторизованного получателя. См. [communication](../communication.md).

## 13. No-solo tests

**Локально PASS:** в 40 authority cases покрыты уникальность и active role lock,
раздельные источники, Engineer-only repair, Sheriff-only report и отсутствие
завершения по одному enum без выданных ссылок/реальной disclosure.

Две содержательные пары сравнивают полный permitted view одной роли, нормализуя
только случайные GUID. В одной сохраняется Engineer view при изменении audit
action и происхождения; в другой сохраняется Sheriff view при изменении скрытого
fault selector и происхождения. Реальные действия совершаются на разных моментах
общего цикла; timestamps/provenance/revisions не исключаются из сравнения.

Strong mechanical hard-negative содержит реальный benign standby write.
Простой natural case без единого player write при complete audit сам по себе
может быть информативен Sheriff; он не выдаётся за доказательство асимметрии.
Во всех шести принятых живых aggregates Engineer получил по одному diagnostic и ни
одного security bundle, Sheriff — по одному security и ни одного diagnostic.
Третий Human в mechanical case получил публичный Resolved, но 0 private bundles
и 0 messages; [fixture](scripted-mechanical-fixture.json) подтверждает observer
проверку. Это подтверждает измеренную recipient separation, а не произвольный
packet-level adversarial тест. Попытки одной роли выполнить обе ветви покрыты
локальной authority suite; отдельный живой одиночный hostile client не заявлен.

## 14. Новые Runtime Skills

Все используют прежние SkillRegistry/Scheduler, typed context, cancellation,
ownership и cleanup; Brain не вызывается для каждого кадра или ожидания.

| `agents-arena.` suffix | Работа |
| --- | --- |
| `select-role` | Серверный выбор duty и коррелированный результат |
| `wait-for-incident` | Ожидание public incident |
| `maintenance-override` | Подход и реальное обслуживание alpha/beta |
| `inspect-cooling` | Engineer private diagnostic |
| `inspect-security-records` | Sheriff private audit |
| `share-investigation-evidence` | Bounded claim через Standard direct message |
| `repair-cooling` | Серверный timed repair до completed |
| `submit-investigation-report` | Проверяемая гипотеза и три ссылки |
| `wait-for-incident-resolved` | Обе authoritative completion branches |
| `cancel-investigation` | Освобождение незавершённой работы |

Terminal Skills используют существующую навигацию/Locomotion2D, серверные комнаты
и реальные подходы. Cleanup выдаёт zero и отменяет незавершённый repair где
доступен transport. Failed/cancelled pre-alarm Objective освобождает duty через
обычный SelectRole(Crew); если incident начался в гонке, используется его свежая
authoritative identity и CancelInvestigation. Успешный role-only Objective и
terminal/Resolved состояние не сбрасываются. См. [Skills](../skills.md).

## 15. Investigation Report

Sheriff выбирает `MechanicalFailure`, `ManualInterference` или
`InsufficientEvidence`. Conclusive report содержит ровно три разных ссылки:
diagnostic EvidenceId, собственный security EvidenceId и один выбранный
MaintenanceRecord.RecordId, плюс bounded explanation. Shotgun citation всех
записей отклоняется.

Проверяются circuit, interval overlap/coverage, controller context, confirmed
action и реальные issued IDs. Diagnostic reference допускается лишь после
фактического receiving-client ACK правильному Sheriff через существующий DM.
ACK не означает чтения/понимания моделью. Максимум три unsupported conclusive
попытки, cooldown 2s; ответ не сообщает правильную причину. InsufficientEvidence
не валидирует report и не разрешает incident.

## 16. Mechanical incident — живой результат

**PASS — `scripted-mechanical-04`.** Оба Objective succeeded и оба клиента
наблюдали один incident в Resolved/Restored с validated report. Offline strict
aggregate прошёл 22/22 checks: identity/session/endpoint/database isolation,
выданные раздельным ролям evidence, подтверждённые repair и report, SQLite
structural redaction. Fixture подтверждает реальную benign standby maintenance,
успешный Crew Objective и Human observer.

Engineer/Sheriff: 45.845/51.906s Objective time, по 7 decisions и 6 successful Skills,
0 failed/cancelled Skills. Engineer получил diagnostic1/security0, Sheriff —
diagnostic0/security1; один repair и один принятый report. Engineer send получил
ServerAccepted1/ClientReceived1, Sheriff получил 1 message. У обоих 0 client
errors/warnings и 0 Runtime errors. [Aggregate](scripted-mechanical.json),
[fixture](scripted-mechanical-fixture.json).

Human сохранил обычный keyboard input и роль Crew: public Resolved/Restored,
report validated, diagnostic/security receipts0/0, own private viewsfalse/false,
messages0. Его финальный DTO после owned cleanup имеет Disconnected; это не
заявление о продолжающейся сессии. Native UI нажатия и чтение панели этим
автоматическим observer proof не подменяются.

Два ранних запуска не засчитаны: `scripted-mechanical-01` завершился
`role_state_invalid`, `-02` — `required_observation_missing`; в каждом неуспешный
SelectRole выдал 0 commands, investigation не начался. Исправления withholding
placeholder state и full owner-state handshake gate описаны в
[initial-startup-failure.json](initial-startup-failure.json). Это не доказательство
устранения любой UDP/network startup-проблемы.

## 17. Manual interference — живой результат

**PASS — `scripted-manual-01`, 22/22 strict checks.** Fixture подтверждает реальную
принятую maintenance-команду ordinary Crew и его successful Objective. Natural
generation была отключена только в trusted серверной подготовке; actual operation
не заменялась командой force-origin. Оба investigation Objective succeeded,
оба клиента подтверждают Resolved/Restored/report validated.

Engineer/Sheriff: 45.263/49.757s, по 7 decisions и 6 successful Skills, 0 failures.
Role-private counts1/0 и0/1, один repair, один validated report,0 report rejects.
У Engineer ServerAccepted1/ClientReceived1, у Sheriff received1. У обоих0
client errors/warnings и0 Runtime errors. [Aggregate](scripted-manual.json),
[fixture](scripted-manual-fixture.json).

## 18. Misleading claim — живой результат

**PASS — `two-codex-manual-claim-02`.** Третий ordinary Crew выполнил реальную
операцию на активном контуре и отправил Sheriff обычный direct message с неверным
механическим объяснением. Fixture подтвердил actual maintenance, успешный Send
Skill и ClientReceived receipt. Сообщение осталось untrusted claim.

Два настоящих CodexBrain собрали собственные разные evidence, обменялись finding,
выполнили ремонт и правильный server-validated report. Оба Objective succeeded,
оба own public views подтверждают один Resolved/Restored incident. Это один
контрольный пример работы вопреки ложному сообщению, не общая гарантия reasoning.
Engineer использует финальный DTO; Sheriff — сохранённый до cleanup own-Resolved
snapshot `supervisor_barrier`, с `exact_owner_session_verified`. Финальные
counters/private-view flags при этом сохранены из последнего client report.
[Aggregate](two-codex-manual-claim.json), [fixture](two-codex-manual-claim-fixture.json).

Предыдущий `-01` остановлен после Crew `route_failed`: обязательная maintenance
не состоялась, incident не начался. Общие room anchors могли быть заняты другим
персонажем. Сервисные Skills теперь используют отдельные authored подходы:
Crew справа, Engineer слева, с нижней staging-точкой Hall и непрерывным проходом
к реальному терминалу. Fresh authoritative stopping и RoomChanged сохраняются.
Это маршруты reference game, не универсальное избегание произвольных игроков.
[Сохранённый неуспех](manual-claim-navigation-failure.json),
[fixture](manual-claim-navigation-failure-fixture.json).

## 19. ScriptedBrain baseline

**PASS:** mechanical и manual scripted baseline приняты отдельно, см.§16–17.
Reference Engineer/Sheriff используют тот же production Skill Engine и typed
capabilities, что Codex. Operator всегда выбирает публичный circuit без чтения
скрытого активного selector. Для живого acceptance необходимы независимые
процессы/identity и совпадающий authoritative outcome, не только exit code.
Именно эти условия проверены 22 acceptance checks каждого aggregate. В каждом
случае записаны четыре различных launch PID, разные ports/credential fingerprints
и database paths. Aggregator проверяет записанную metadata; поле
`actual_live_pids_verified=false` честно указывает, что он не инспектирует ОС.
Серверный fixture остаётся отдельным proof реального взаимодействия через VPS.

## 20. Mixed Brain tests

**PASS — Codex Engineer + scripted Sheriff, `mixed-codex-engineer-01`.** Все 22
strict checks true; Fixture подтверждает actual maintenance и successful Crew.
Оба investigator Objective succeeded, один incident Resolved/Restored с report
validated. Engineer: 7 реальных Codex turns, 6 successful Skills, 71.030s.
Scripted Sheriff: 7 decisions, 6 successful Skills, 70.168s. У обоих 0 failures,
Runtime/client errors и client warnings. [Aggregate](mixed-codex-engineer.json),
[fixture](mixed-codex-engineer-fixture.json).

**FAIL сохранён — scripted Engineer + Codex Sheriff, `mixed-codex-sheriff-01`.**
18/22 checks true; false: both resolved, both Objective passed, both report
validated, Sheriff report confirmed. Repair завершился, но report не был
валидирован. Sheriff сделал 13 реальных turns/14 decisions, 13 Skill calls:
10 succeeded, 2 failed, 1 timed_out. Фиксированы rejected private-recipient send,
message timeout и один `report_not_supported`; ответы не раскрыли правильную
причину. Два report Skill calls не равны двум semantic report rejections.

Engineer завершился failed после 262.510s, Sheriff — failed после 267.552s.
Final captured client states различаются по моменту: Engineer Cancelled,
Sheriff Active, оба Restored и report not validated. Это не accepted success.
Client errors/warnings0/0 у обоих, поэтому failure нельзя объяснять падением
Unity по этим счётчикам. [Сохранённый aggregate](mixed-codex-sheriff-failed.json),
[fixture](mixed-codex-sheriff-failed-fixture.json).

**FAIL до расследования — mixed Sheriff02, headless.** Codex Sheriff не сделал
ни одного model turn/Skill/command; его Objective failed за 6.356s. Scripted
Engineer был cancelled за 3.943s при общей очистке. Non-rendering loop сообщал
тысячи updates/s; это не игровая отрисовка и не performance success.
[Безопасный failed aggregate](headless-startup-failure.json).

Диагностика выявила синхронный cold import SDK до первого await; он мог
блокировать heartbeat loop. Импорт вынесен в worker thread; отдельный loopback
gRPC test проверяет heartbeat и отмену во время импорта. Headless Player получил
cap60 вместо uncapped loop. Таймауты bridge сохранены.

`mixed-codex-sheriff-03` завершил оба Objective и server barrier, однако финальный
DTO Sheriff потерял public status при despawn во время cleanup. Его strict
aggregate сохранён как FAIL пяти lifecycle checks; результат не восстановлен
задним числом. Launcher теперь сохраняет contemporaneous own-success/own-Resolved
snapshot до cleanup. Fallback допустим только для полностью очищенного DTO с
точным ActorId/accepted session/Runtime identity; counters и private-view flags
берутся из финального отчёта. Неверные/чужие/failed snapshots покрыты тестами.
[Cleanup-race aggregate](mixed-codex-sheriff-cleanup-race.json),
[ранний fixture](mixed-codex-sheriff-cleanup-race-fixture.json).

**PASS — scripted Engineer + Codex Sheriff, `mixed-codex-sheriff-04`.** Все22
strict checks true и fixture passed. Sheriff сделал7 реальных turns, без
report rejection. Оба Objective succeeded и оба финальных DTO сами содержат
Resolved/Restored/validated; snapshot fallback для этого pass не понадобился.
[Aggregate](mixed-codex-sheriff.json), [fixture](mixed-codex-sheriff-fixture.json).

## 21. Two-Codex multiplayer test

**PASS для обоих происхождений.** `two-codex-mechanical-01` содержит реальную
benign standby maintenance; `two-codex-manual-claim-02` — active maintenance и
ложный Crew claim. В каждом случае два настоящих CodexBrain, отдельные процессы
Runtime/Unity, fresh SQLite, bridge credentials, ports, accepted sessions и
собственные provider threads. Ни один Brain не получил trusted scenario config.

Оба strict aggregates прошли22/22 checks; оба Objective succeeded, совпадают
public incident identity и Resolved/Restored/reportValidated. Четыре живых
Runtime/Unity процесса и разные официальные Codex descendants проверены через
OS во время каждого запуска; опубликованы только boolean predicates.
Offline summarizer сам не инспектирует живые PIDs и честно сохраняет этот факт.
[Mechanical](two-codex-mechanical.json), [manual](two-codex-manual-claim.json),
[live process proof mechanical](live-processes-mechanical.json),
[live process proof manual](live-processes-manual.json).

## 22. Codex turns каждого агента

| Принятый case | Engineer Codex turns | Sheriff Codex turns |
| --- | --- | --- |
| Scripted mechanical | scripted (7 decisions) | scripted (7 decisions) |
| Scripted manual | scripted (7 decisions) | scripted (7 decisions) |
| Mixed Codex Engineer | 7 | scripted (7 decisions) |
| Mixed Codex Sheriff | scripted (7 decisions) | 7 |
| Two-Codex mechanical | 8 | 7 |
| Two-Codex manual + claim | 8 | 7 |

Это реальные provider turns, не frame ticks и не список заранее выбранных Skills.
Failed Sheriff01:13 turns; headless startup02:0. Полная usage-история ниже.

## 23. Message/evidence exchange metrics

| Case | E diag/security | S diag/security | E share calls | E accepted/received ACK | E/S messages received | S mean latency,ms |
| --- | --- | --- | --- | --- | --- | --- |
| Scripted mechanical | 1/0 | 0/1 | 1 | 1/1 | 0/1 | 127.000 |
| Scripted manual | 1/0 | 0/1 | 1 | 1/1 | 0/1 | 1180.000 |
| Mixed Codex Engineer | 1/0 | 0/1 | 1 | 1/1 | 0/1 | 122.000 |
| Mixed Codex Sheriff | 1/0 | 0/1 | 1 | 1/1 | 1/1 | 110.000 |
| Two-Codex mechanical | 1/0 | 0/1 | 1 | 1/1 | 1/1 | 108.000 |
| Two-Codex manual + claim | 1/0 | 0/1 | 1 | 1/1 | 1/2 | 344.000 |

Share call и ACK — отдельные измерения. Стандартный Share Skill передаёт одну
ссылку на собственный diagnostic bundle; receipt не доказывает понимание Brain.
Произвольные упоминания reference IDs в иных сообщениях здесь не подсчитываются.
Cross-host latency — приблизительная разница wall clocks, не RTT. Значение1180ms
в scripted manual сохранено. Для claimant дополнительно проверен ACK его own Send.

Все принятые SQLite artifacts прошли structural redaction: чужих own bundles нет,
private payload fields redacted. Содержимое сообщений, улик и их IDs не публикуется.

| Case | E/S memory retrievals | E/S replans |
| --- | --- | --- |
| Scripted mechanical | 7/7 | 0/0 |
| Scripted manual | 7/7 | 0/0 |
| Mixed Codex Engineer | 7/7 | 0/0 |
| Mixed Codex Sheriff | 7/7 | 0/0 |
| Two-Codex mechanical | 8/7 | 0/0 |
| Two-Codex manual + claim | 8/7 | 0/0 |

## 24. Неправильные hypotheses

| Case | E/S rejected reports | E/S failed or timed-out Skills | E/S cancelled Skills |
| --- | --- | --- | --- |
| Scripted mechanical | 0/0 | 0/0 | 0/0 |
| Scripted manual | 0/0 | 0/0 | 0/0 |
| Mixed Codex Engineer | 0/0 | 0/0 | 0/0 |
| Mixed Codex Sheriff | 0/0 | 0/0 | 0/0 |
| Two-Codex mechanical | 0/0 | 1/0 | 0/0 |
| Two-Codex manual + claim | 0/0 | 0/0 | 0/0 |

Каждый accepted case получил правильный server-validated conclusive report.
Failed Sheriff01: один `report_not_supported`, два report Skill calls и ещё
recipient rejection/message timeout. Это разные виды ошибок. Число ошибочных
proposed_origin отдельно не экспортируется: unsupported citation/report нельзя
автоматически приравнять неверной классификации или ложному обвинению в тексте.
Полная история разработки:13 pair attempts,6 strict accepted pairs; среди25
записанных terminal investigator Objectives14 succeeded,7 failed,4 cancelled
(56% Objective success); для одного Engineer terminal report отсутствует.
Включены interrupted fullscreen run и successful Objectives с cleanup-measurement
failure; deliberate fault tests и auxiliary Crew не включены. Это итерации разных
ревизий, не accuracy frozen model или оценка вероятности успеха следующего запуска.
[Точные границы и статусы](evaluation.json).

Гипотезы скрытого reasoning не сохраняются. Unit tests проверяют cooldown и
Failed после третьей неподдержанной conclusive попытки.

## 25. Objective duration

| Case | E/S Objective,s | E/S Skills total,s | E/S context max,bytes |
| --- | --- | --- | --- |
| Scripted mechanical | 45.845/51.906 | 45.317/51.331 | 15178/14501 |
| Scripted manual | 45.263/49.757 | 44.907/49.373 | 15013/14320 |
| Mixed Codex Engineer | 71.030/70.168 | 29.051/69.758 | 15014/14325 |
| Mixed Codex Sheriff | 60.891/68.337 | 60.556/26.854 | 14837/13788 |
| Two-Codex mechanical | 74.395/72.383 | 31.378/33.789 | 14843/15929 |
| Two-Codex manual + claim | 71.790/76.131 | 35.724/30.761 | 14842/15946 |

Таймеры принадлежат отдельным параллельным Objective. Их нельзя складывать в
wall time пары. Отдельный supervisor wall interval не измерялся. Failed Sheriff01
занял262.510/267.552s; startup/cleanup/navigation failures сохранены отдельно.

## 26. Codex usage

| Codex case/role | Turns | Input | Cached input | Output | Total | Turn seconds |
| --- | --- | --- | --- | --- | --- | --- |
| Mixed Codex Engineer engineer | 7 | 206460 | 166528 | 655 | 207115 | 34.588 |
| Mixed Codex Sheriff sheriff | 7 | 211553 | 170624 | 1169 | 212722 | 37.811 |
| Two-Codex mechanical engineer | 8 | 255415 | 210688 | 1066 | 256481 | 38.997 |
| Two-Codex mechanical sheriff | 7 | 211413 | 169728 | 924 | 212337 | 32.820 |
| Two-Codex manual + claim engineer | 8 | 253388 | 209792 | 818 | 254206 | 32.528 |
| Two-Codex manual + claim sheriff | 7 | 212523 | 170752 | 1151 | 213674 | 38.845 |
| Sheriff01 FAIL sheriff | 13 | 546513 | 476416 | 4255 | 550768 | 117.301 |
| Sheriff03 cleanup proof FAIL sheriff | 7 | 211806 | 169984 | 1001 | 212807 | 35.563 |
| Manual claim01 route FAIL engineer | 3 | 79827 | 39040 | 404 | 80231 | 18.199 |
| Manual claim01 route FAIL sheriff | 3 | 79678 | 55424 | 461 | 80139 | 20.288 |

Provider-reported cached input входит в input и не прибавляется повторно.
У scripted usage unavailable. Ранний headless startup02 не произвёл model turns.
In-flight cancellation и SDK usage могут иметь разные моменты финализации;
публичные значения взяты из окончательных агрегатов, а не оценки стоимости.

Внешний игровой контекст (`context.external_json()`) ограничен16384 UTF-8
bytes и bounded evidence/event/memory selection. Это не размер полного model
prompt: objective, trusted installed catalog и schema учитываются отдельно. Профиль [investigation-config.json](../investigation-config.json)
содержит только лимиты. Budget500000 проверяется перед следующим turn: завершение
одного turn может перейти порог, как550768 в failed Sheriff01. Это не жёсткий
лимит расходов API. Подробные mean/median/p95 provider и decision latency находятся в safe JSON
каждого case; таблица выше показывает сумму model turn duration.
Hidden chain-of-thought и денежные оценки не публикуются.

## 27. Unity/networking performance

**Локально PASS:** 58 world geometry checks, 12 prediction/query checks, два новых
терминала и три свободных approach poses. Проверены оба направления passage,
диагонали, 16 spawn slots, solid walls/reactor и escape прежней зажатой позы.
См. [world-and-prediction.json](world-and-prediction.json).

Это Editor physics/query proof. Дополнительно actual VPS movement regression
прошёл13/13 checks: local visual response32.351ms, authoritative root
response323.754ms, local release99.149ms, idle reconciliation error0.004976,
errors0/warnings0. Probe работал windowed800×600 с synthetic input, без
искусственной network delay; собственный клиент закрыт автоматически.
Это один run, **не native keyboard-to-render measurement** и не универсальная
оценка сети. [movement.json](movement.json).

| Принятый case | E/S mean FPS | E/S command RTT median/p95,ms | E/S max Pump,ms |
| --- | --- | --- | --- |
| Scripted mechanical |112.972/113.532|6.287/12.290;5.360/9.143|4.217/6.810|
| Scripted manual |117.569/117.973|4.824/9.209;5.182/9.321|4.134/4.510|
| Mixed Codex Engineer |117.426/117.112|5.022/8.732;5.709/9.748|5.345/3.943|

RTT здесь Runtime→локальный adapter command-result, не RTT до VPS.
FPS — среднее за конкретный client report interval, не controlled benchmark
против M5. У Human observer mean114.579FPS за65.632s,0 errors/warnings.
Two-Codex cases выполнены без окон, с cap60. Их update rate около60/s не
является rendered FPS. Локальный command-result RTT в этих cases ограничивается
также фазой main-thread pump; он не измеряет сеть VPS. Controlled rendered
benchmark под двумя Codex и native Human keyboard-to-render не выполнялись.
Motor, owner prediction и camera bounds сохранены; сервисная навигация использует
отдельные authored полосы прохода.

`scripted-mechanical-03` был прерван из-за полноэкранных окон, мешавших работе
пользователя; все 4 собственных fullscreen clients закрыты. Это не accepted pass
и не диагностированный cognitive failure. Все последующие окна явно windowed:
800×600, fault clients640×360. Длительные automated cases допускают headless
без graphics device; fullscreen запрещён. См. [window-mode.json](window-mode.json).

## 28. Security/permission tests

**Локально PASS:** чужая роль/recipient/identity, неизвестный или stale match/
incident, чужие/expired evidence IDs, no-solo role switching, неверный ACK sender/
recipient/task, sensitive station broadcast, malformed/nonfinite/distant poses,
missing/moved terminal, typed cross-command correlation conflict, replay,
per-actor/global rate limits, record/ref bounds и terminal cleanup.

Protocol tests отдельно проверяют отсутствие private payload/origin в public
schemas и сохранение двух фаз repair с одной correlation. Live role-private
receipt separation и Human public-only view подтверждены в§13/16. В failed
mixed Sheriff сервер также фактически отклонил сообщение с private reference
неразрешённому получателю; это failure работы агента и успешное применение
permission gate, не успешное завершение расследования.

Три отдельные реальные fault cases — **PASS**, [safe projection](faults.json):

| Сбой | Время до safe terminal sample,s | Deadline,s | Наблюдение |
| --- | --- | --- | --- |
| Abrupt Engineer Runtime SIGKILL |0.992197|15|Его клиент Error; Sheriff failed/Cancelled, stationary, keyboard disabled |
| Abrupt Sheriff client SIGKILL |30.733107|45|NGO timeout30s; Engineer failed/Cancelled, stationary |
| Только собственный dedicated server SIGTERM |3.532391|45|Оба Objective failed/session_disconnected, оба клиента Disconnected |

В каждом fixture проверил exact ownership цели, тот же active incident до
сбоя, cleared foreground у живого Runtime и стабильность command count ещё1s.
Все owned processes и собственные fault servers очищены.
Финальная read-only сверка106 записей Runtime/client PID по exact report paths
не нашла оставшихся процессов тестовых cases; [cleanup.json](cleanup.json). Last report убитого процесса
помечен stale и не используется как доказательство его продолжающейся работы.
Времена — safety convergence, не движение/keyboard response latency. Auxiliary
Crew не объявляется terminal в sampled instant. Отдельные native unauthorized
UI/packet tests и новые cases отмены Objective на VPS не приписываются этим
трём сбоям.

## 29. Automated test counts

| Проверка | Подтверждённый результат | Граница доказательства |
| --- | --- | --- |
| Runtime, свежий установленный wheel |403/403,59.08s|Без Codex/Jev SDK и model auth; [package.json](package.json) |
| SDK/bridge C# |162/162|Включает40 IncidentAuthority,7 IncidentProtocol и42 CooperativeAuthority cases |
| Control Plane C# |18/18|Admission/process lifecycle |
| Скриптовые unittest |47/47|Summary, snapshot identity/cleanup guards и независимые launch plans |
| Generated artifact freshness |52/52|Bindings/descriptors/manifests совпадают |
| Investigation fault helper self-tests |6/6|Offline guards; не реальные faults |
| Существующий M5 fault helper self-tests |9/9|Offline guards; не новый M6 fault proof |
| Routing regression selection |32/32|Локальные routing/preemption регрессии |
| Owner prediction/query |12/12|Editor, не end-to-end keyboard latency |
| World geometry |58/58|Editor authored capsule/physics |
| Actual VPS movement probe |13/13|Synthetic input,windowed; [movement.json](movement.json) |
| Actual investigation fault cases |3/3|Не offline self-tests; [faults.json](faults.json) |

Счётчики разных уровней не суммируются в один искусственный total: выбранные
routing tests входят в общий Runtime suite, а параметризованные subTest variants — внутрь
скриптовых методов. Локальные сборки/tests не заменяют пункты16–21.

## 30. Existing regression status

**PASS:** существующие SDK/Runtime/Control Plane/schema, routing, geometry,
prediction и calibration проверки. Новый live movement probe13/13 и Human
public/private observer подтверждены выше. Новая calibration-регрессия прошла
через обычный VPS lobby на0.7.0: оба Objective succeeded и оба own authoritative
task states Completed. [Strict aggregate](calibration-regression.json).

Существующие capabilities calibration/StationSignal/communication сохраняются.
Core/Standard schemas, movement motor и NetworkCharacter совпадают с baseline;
проверены байты всех46 исходных PNG, original prefab и offline MainHall.
[Source invariants](source-invariants.json). Исторические [M5 результаты](../cooperative-validation/README.md)
остаются отдельными. Новые native Human UI клики/чат не автоматизировались,
чтобы не перехватывать рабочий ввод пользователя. Автоматические наблюдения и
synthetic movement probe не выдаются за этот manual UI тест.

## 31. VPS deployment status

**Подтверждено:** game и Control Plane release `20261019T194337Z-88a9df1`,
gameVersion0.7.0/admission3, health healthy. Safe deployment artifact фиксирует
server executable SHA-256
`c9724c53ae3bc7a0ac1143ab71679b777055196707c1f2be3b65de355cd46e82`
и game assembly SHA-256
`7d4c2bf2d7175eb2315924e9787da6d69ceec7a173b973e9d4752b6ba94a785b`.
Публичного Runtime bridge listener нет (`runtime_port_listening=false`).
[deployment.json](deployment.json).

Эти версии относятся к actual VPS cases. Финальный macOS Player пересобран
с client-only cap60 для batch mode, source `73b8d13`; executable/assembly hashes
также записаны в deployment artifact. Серверная authority и protocol с момента
deployed88a9df1 не изменялись. Runtime остаётся локальным; Codex/Runtime на VPS
не устанавливались. Public HTTP deployment не имеет production TLS hardening.

## 32. Git commits

Исходная база milestone: `1236ab6`. Существующие code commits:

- `170b701` — authority, private evidence и role-based cooling investigation.
- `e3ca70c` — withholding owner-state placeholders.
- `302c727` — full reference-game state gate перед bridge handshake.
- `88a9df1` — освобождение pending duty при cancelled Objective; deployed game release.
- `718dd96` — windowed clients и явные readiness checks.
- `fff2d1f` — механика investigation в Skill metadata, bounded disclosure TTL и headless launch.
- `4f9c638` — неблокирующий optional SDK import и heartbeat regression.
- `73b8d13` — client-only pacing60 для batch Player; пересобран macOS client.
- `5ce32e3` — строгие own completion snapshots до cleanup.
- `52a9959` — сервисные маршруты вокруг занятых общих anchors.
- `0e4607e` — measured replans в безопасных metrics; финальный functional source в main.

Functional source `0e4607e` опубликован в `main`. Итоговые документы и safe
validation artifacts публикуются следующим отдельным commit. Game release,
локальный Runtime wheel и documentation commit различаются явно; точные binary
hashes находятся в deployment evidence.

## 33. GitHub CI status

**PASS для финального functional source `0e4607e`:**
[Control Plane](https://github.com/fpetrov/Actor/actions/runs/39319802473) и
[SDK/Runtime](https://github.com/fpetrov/Actor/actions/runs/39319802455), оба
completed/success. Это точный M6 commit после исправления сервисных маршрутов,
а не прежний baseline. [Зафиксированные CI результаты](ci.json).

Документация и safe validation artifacts следуют отдельным commit. Его main CI
проверяется после публикации; этот файл фиксирует проверку functional source.
[Текущие main runs](https://github.com/fpetrov/Actor/actions?query=branch%3Amain).

## 34. Ограничения

Один investigation на match; terminal outcome не перезапускается в том же lobby.
Нет durable восстановления активного дела, cross-lobby evidence archive,
handoff required duty во время Active, shared cognition или общей памяти агентов.
Разрыв multiplayer обнаруживается в пределах существующих NGO timeout, а не
мгновенно. Distance/lease membership проверяет игровой сервер в своей bounded
каденции; локальные тесты не доказывают произвольные сетевые условия.

Сценарий ограничен двумя контурами и небольшим источником записей. Strong
asymmetry доказана парными тестами с настоящим standby write; natural no-write
case проще. Correct report — верифицируемый игровой результат, не доказательство
свободного социального reasoning во всех мирах. Typed receipt не доказывает
понимание, private recipient не гарантирует последующее неразглашение им.

Шесть accepted investigation cases, три real faults и movement/calibration
regressions — ограниченная выборка. Она не скрывает начальные readiness failures,
прерванный fullscreen запуск, failed Sheriff01, cold SDK import02, cleanup-report
race03 и navigation failure с третьим Crew. Последние исправления проверены
повторными actual runs и regression tests. Universal dynamic avoidance, native
Human UI и controlled rendered performance под двумя Codex здесь не доказаны.

## 35. Рекомендуемый следующий milestone

Следующий шаг — ограниченная cooperative belief model с явным разделением
own observations, полученных claims и проверенных выводов, плюс безопасный
role handoff после departure/terminal cleanup. Нужны provenance, uncertainty,
обновление/отзыв убеждений и тестируемый lifecycle передачи обязанности.

Это развитие после принятого текущего вертикального среза. Оно не включено
скрыто в M6, не требует вводить игровые роли в Core и не заменяет завершение
реальных VPS, Human и fault проверок настоящего milestone.
