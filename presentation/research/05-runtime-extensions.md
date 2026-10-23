# ACTOR: дополнительные игровые модули Runtime и практика доказательств

Дата: 23 октября 2026 года. Снимок: `aa5d46ec1fee4fbcab9292fc1f58f48fee3e1bb8`. Дополнение к [отчету о классификаторе](/private/tmp/actor-classifier-research.md). Работа только на чтение: исходный Actor не изменялся, git fetch/pull не выполнялись, тесты, модели, Unity, серверы и launchers не запускались. Изменения сделаны только в этом исследовательском файле в `/private/tmp`.

## Место Arena в архитектуре

`runtime/actor_agents_arena` содержит конкретную игровую семантику и процедуры. Core не знает комнат, расследований, Engineer/Sheriff, Crew/Impostor и маршрутов станции. Extension package регистрирует generated Protobuf types и канонические descriptors через общий ExtensionRegistry. Entry points подключают Skills, правила событий, match policy, Brains и game launch profile.

В Arena есть две разные среды проверки:

1. Sandbox: ранний двухкомнатный маршрут Main Hall ↔ Engineering, кооперативная калибровка и ролевое расследование.
2. SocialDeduction: четыре целевые комнаты Main Hall, Engineering, Security, Storage и проходной east-corridor; скрытая сторона, задачи, убийства, собрания, голосование, аварии и публичный терминальный итог.

Это не две конкурирующие реализации Runtime: обе составлены из одних интерфейсов Session, Scheduler, Skills, EventRouter, Brain и Standard Communication. Отличаются negotiated game capabilities и установленная игровая политика.

Опорные файлы: [__init__.py:82](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/__init__.py:82), [skills.py:207](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/skills.py:207), [social_navigation.py:245](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_navigation.py:245), [docs/skills.md:10](/Users/fpetrov/PycharmProjects/Actor/docs/skills.md:10).

## Карта Python-модулей

| Модуль | Назначение и граница |
|---|---|
| `__init__.py` | Имена game capabilities, generated room/station/calibration/incident/social types; сборка ExtensionPackage из packaged JSON/descriptor resources. Валидация типов остается в общем registry. |
| `skills.py` | Production Sandbox NavigateToRoom, authored waypoints и service approaches; строгое подтверждение прибытия, владение locomotion, остановка в cleanup. Подключает calibration Skills. |
| `scripted.py` | Исторический прямой route spike над RuntimeSession: Main Hall → Engineering → Main Hall. Без Brain/Skill framework, но с typed observations/commands и авторитетными transitions. Не следует представлять его как текущую интеллектуальную стратегию. |
| `demo_brain.py` | SequenceBrain для той же демонстрации: Navigate Engineering → Wait(2) → Navigate Hall → Finish. В этом старом Brain три Skill-решения, в новом плане ту же последовательность можно принять одним решением. |
| `calibration.py` | Start/assignment/inspect/submit/wait/cancel кооперативной калибровки; role/task/match/correlation/freshness checks; приватная authorization; bounded finalizer. |
| `cooperative_brain.py` | Два явных детерминированных reference providers, coordinator и technician. Заранее задана процедура, но dynamic IDs и код берутся только из собственного permitted context. |
| `events.py` | Typed station/calibration policies: routine notices, urgent recall, private diagnostic и assigned-task terminal transitions. Не использует ключевые слова сообщения как авторитет. |
| `investigation.py` | Role selection, incident waiting, maintenance operation, private inspections, repair, cited report, evidence sharing, resolution wait и cleanup. |
| `investigation_brain.py` | Детерминированные Engineer/Sheriff/maintenance/claimant fixtures; сопоставление received diagnostic claim и own audit. Не скрытый oracle. |
| `investigation_events.py` | Проверка собственного recipient/role/match/provenance/expiry; private evidence session-only; lifecycle preemption; bounded correlated inboxes. |
| `social_agent.py` | SocialMatchPolicy: phase/life/connection/discovery boundaries, catalog filtering, phase-specific context selection, WorkingKnowledge. Отдельный SocialScriptedBrain — reference strategy. |
| `social_skills.py` | Типизированные игровые действия: readiness, реальные/fake tasks, kill, report, meeting, vote, sabotage, repair, approach, evade и bounded wait. Server outcome обязателен. |
| `social_navigation.py` | Dijkstra по публичному authored graph, real own-pose connectors, ограниченные reroutes/backtrack и остановка. Нет скрытого actor tracking. |
| `social_pursuit.py` | Bounded visible following/approach, локальное избегание только видимых peers, public floor envelope, короткий fixed last-seen grace. Само не убивает и не определяет сторону. |
| `social_discussion.py` | Send + server receipt + короткий batch claims; либо чтение ранних/новых replies без отправки. Deadline reserve и отсутствие model polling внутри Skill. |
| `social_events.py` | Own-recipient/match/type/bounds policy, session-only active data, критические typed transitions, public postgame только после GameOver. |
| `social_fixtures.py` | Реальные игровые детерминированные сценарии vote/parity/sabotage/tasks/privacy. Отдельные factory names и явно ограниченные доказательства mechanics/integration. |
| `launcher.py` | GameLaunchProfile для установленного actorctl: version 0.10.0, обычные client flags, 800×600 windowed/headless options, проверка public player name. Generic launcher не знает Arena UI/ролей. |

В `social_layout.json` прочитана структура и проверены списки: 18 floor polygons, 23 nodes, 23 edges, 6 terminals; floor room IDs — main-hall, engineering, security, storage, east-corridor. Это публичная авторская геометрия, а не текущие позиции игроков. Координаты каждого полигона в этом дополнении отдельно не интерпретировались.

## Навигация и действие не равны телепорту

Sandbox NavigateToRoom читает Transform, Motion и RoomMembership. Проверяет конечность чисел и соответствие Transform/Motion одному кадру. Waypoint считается достигнутым после четырех различных свежих stopped samples; один cached sample не может стать четырьмя. Переход между комнатами требует настоящего RoomChanged event. На всех выходах navigation cleanup отправляет нулевой locomotion intent.

Service routes специально обходят общие occupied anchors: отдельные коридорные полосы ±0,3 и подходы к панелям. Это конечная authored схема конкретной станции, не универсальная navigation mesh служба.

Social navigation добавляет публичный граф и Dijkstra. Сначала агент физически достигает собственного connector anchor. При отсутствии прогресса более двух секунд по новым own frames ребро блокируется, агент возвращается к последнему реально достигнутому узлу и пробует альтернативу; максимум два reroute. Нельзя просто выбрать ближайший узел по другую сторону препятствия и объявить прогресс. Для первоначального connector есть максимум три различных физически проверенных кандидата своей комнаты и короткий deterministic stagger по собственному ID. Phase, own life, can_move и authenticated connection generation ограничивают действие.

Visible pursuit использует own root + публичный capsule offset 0,46 м и только текущий visible-world bundle. Малый velocity lead разрешен только пока цель видна. После потери видимости применяется фиксированная последняя доставленная точка, default grace 1,2 с, максимум 3 с, без экстраполяции скрытой цели. Для actual opportunity требуется четыре разных stopped observations в configured range. Функция не выбирает жертву и не отправляет kill; следующий Skill/Brain решает отдельно. Physical capsule envelope учитывается при проверке коротких шагов и контактной дистанции.

Источники: [skills.py:48](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/skills.py:48), [skills.py:84](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/skills.py:84), [skills.py:135](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/skills.py:135), [social_navigation.py:41](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_navigation.py:41), [social_navigation.py:95](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_navigation.py:95), [social_navigation.py:168](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_navigation.py:168), [social_pursuit.py:87](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_pursuit.py:87), [social_pursuit.py:104](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_pursuit.py:104).

## Калибровка: доказательство настоящей кооперации

Coordinator и technician — разные server-approved actors. Coordinator в Main Hall получает short-lived authorization из приватного terminal result и должен передать ее technician через обычное sensitive direct сообщение. Technician подходит к console Engineering, отправляет authorization и ждет авторитетный Completed. Server дополнительно требует actual permitted delivery: просто угадать код или прочитать публичный progress недостаточно.

Private assignment содержит участников, expiry и attempts. Public progress отделен от кода и private evidence. Нормальная последовательность Available → AwaitingDiagnostic → AwaitingAuthorization → Completed; Failed/Expired/Cancelled — другие терминальные исходы. Local command acceptance, достижение панели и server message acceptance сами по себе не означают Completed.

Каждый action несет correlation и match identity. Inspect проверяет task assignment, room, source result, sensitive flag и expiry, регистрирует authorization как известный секрет. Submit требует правильную technician assignment и после correlated result ждет observed Completed. Wait не вызывает Brain polling. Finalizer завершившегося Objective через тот же Scheduler отменяет незавершенную собственную assignment; уже завершенное состояние не стирает. Он учитывает новые terminal events, которые могут опередить periodic observations.

Детерминированные Brains используют собственный roster/assignment/result/message. Coordinator предпочитает единственного peer с публичным example name Technician, если он есть в permitted roster; это тестовая эвристика выбора партнера, не скрытый канал. Код очищается при interrupt/close. Реальный Codex остается отдельным catalog-driven provider без заранее вставленной процедуры.

Источники: [docs/cooperative-tasks.md:7](/Users/fpetrov/PycharmProjects/Actor/docs/cooperative-tasks.md:7), [calibration.py:97](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/calibration.py:97), [calibration.py:176](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/calibration.py:176), [calibration.py:199](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/calibration.py:199), [calibration.py:313](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/calibration.py:313), [cooperative_brain.py:31](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/cooperative_brain.py:31).

## Расследование: факты разных ролей

Engineer получает приватную техническую диагностику: failed circuit, bounded failure window, pressure samples, wear, controller revision pair. Sheriff получает собственный bounded security audit с покрытием circuits/time и конкретными maintenance records. Ни одна сторона не получает actual_cause. Нужны обмен через Standard Communication, физический repair Engineer и conclusive validated report Sheriff.

Происхождения: mechanical_failure и manual_interference; insufficient_evidence — явный неконклюзивный отчет. Его принятие не равно report_validated и не разрешает incident. Report должен содержать ровно три различных ссылки: diagnostic EvidenceId, собственный security EvidenceId и один конкретный maintenance RecordId. Server проверяет происхождение выданных свидетельств и реальное разрешенное cross-role disclosure; угадывания enum недостаточно.

Correlation использует circuit, fault window, confirmed action и controller context. Wear, падение давления и неизменный R→R встречаются при обеих причинах. Access/Inspect не равны manual interference. Нужна достаточная полная audit coverage. Paired hard negatives держат одну роль неизменной, меняя другую и правильный origin. Это не произвольное социальное понимание мира: ограниченная верифицируемая игровая задача.

Private evidence проверяется по recipient, role, incident/match, expiry и provenance и хранится как session_only. ShareInvestigationEvidence строит bounded JSON claim из собственной диагностики и посылает Sheriff через обычный SendMessage; у получателя это все равно received claim, а не собственное наблюдение. Repair ждет completed result того же correlation и observed restored state. Cleanup отменяет только собственный pending repair и обнуляет movement. WaitForIncidentResolved требует одновременно Resolved, Restored и report_validated.

Role selection заперта во время активного incident; один actor не выполняет обе ветви. Finalizer освобождает неоконченное дело через typed cancel, а при failed/cancelled pre-alarm objective пытается вернуть duty в Crew. Successful role-only objective намеренно сохраняет duty. Race между началом incident и освобождением duty обрабатывается по реальной авторитетной identity.

Reference Engineer/Sheriff Brains имеют фиксированный порядок процедур. MaintenanceBrain делает реальную публичную alpha operation без чтения hidden active circuit. Claimant вариант дополнительно отправляет заранее заданную misleading claim; это fixture для проверки обработки чужого утверждения. Такой успех не доказывает unrestricted deception resistance реальной модели.

Источники: [docs/investigation.md:13](/Users/fpetrov/PycharmProjects/Actor/docs/investigation.md:13), [investigation.py:68](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/investigation.py:68), [investigation.py:130](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/investigation.py:130), [investigation.py:195](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/investigation.py:195), [investigation.py:377](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/investigation.py:377), [investigation.py:430](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/investigation.py:430), [investigation.py:496](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/investigation.py:496), [investigation.py:579](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/investigation.py:579), [investigation_brain.py:25](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/investigation_brain.py:25).

## Social match policy, Skills и discussion

SocialMatchPolicy объясняет обе стороны одной role-neutral static инструкцией. Она не выбирает victim, ballot, alibi или winner. Реальная сторона, duty/life/timers приходят из собственного observation. Policy выбирает небольшой набор context capabilities по фазе: exploration включает собственный Transform/visible world/tasks/emergency, discussion/voting — meeting state, waiting — player readiness. Неактуальные motor/task панели не отправляются модели только ради истории.

Episode key включает phase_revision, own life, assignment, connection_generation и discovery digest тела. Typed lifecycle event может остановить старую работу раньше следующего frame; новая когнитивная работа ждет coherent phase/generation observation. Dead/nonalive/stale own state приостанавливают model work. GameOver — только authoritative terminal, с bounded ожиданием postgame summary. Активный принятый plan с body condition удерживает body discovery digest, чтобы planned response не был отменен лишним episode restart.

Policy observe создает исторические last-seen/body records и player_claim с автором, sequence, revision, временем/expiry. Исчезновение игрока убирает его current pose, но не переписывает историю. Current visible attack/activity и historical knowledge различаются. Hypothesis и contradiction остаются отдельными uncertain records с ссылками, а не verified Alignment.

SocialActionSkill создает action context: match, phase revision, correlation, expiry и connection generation. Results связываются по match/correlation/action; work acceptance отличается от completion. Task Skills выполняют те же displayed mechanics, что Human: hold, timed circuit choice либо two-sample sensor trend. Только own targeted work prompt дает step choice; отдельный step receipt не заменяет completion исходного work request. Каждый step посылается один раз по work_id/step_revision и не раньше not_before. Cleanup отменяет только собственный work ID/start correlation.

ReportBody требует currently visible body и реальную дистанцию; может физически приблизиться и повторно проверить видимость. ReportVisibleBody выбирает ближайшее текущим собственным наблюдением тело и подходит как предвыбранная реакция плана. CallMeeting/Sabotage сначала должны найти currently visible terminal. CastVote требует текущий meeting ID, открытую фазу, неотправленный собственный ballot, deadline и eligible candidate либо Skip. Server независимо проверяет действие.

Discuss отправляет выбранный моделью текст обычной Standard командой, ждет реального ServerAccepted и собирает короткий burst ответов. AwaitDiscussionUpdate ничего не посылает. Default wait 12 с, settle 1,5 с, max 6 claims и reserve 8 с перед discussion deadline; параметры ограничены. Ранние сообщения из bounded inbox не теряются. Claims возвращаются с author/message/server sequence/time/expiry и `unverified_player_claim`. Сам Skill не вызывает модель, не определяет виновного и не выбирает ballot. ClientReceived не означает BrainRead или согласие с содержанием.

Источники: [social_agent.py:18](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_agent.py:18), [social_agent.py:42](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_agent.py:42), [social_agent.py:57](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_agent.py:57), [social_agent.py:141](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_agent.py:141), [social_skills.py:55](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_skills.py:55), [social_skills.py:149](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_skills.py:149), [social_skills.py:199](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_skills.py:199), [social_skills.py:286](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_skills.py:286), [social_discussion.py:19](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_discussion.py:19), [social_discussion.py:65](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_discussion.py:65).

## Что делают fixture Brains

SocialScriptedBrain — простой reference provider, не доказательство model intelligence. SocialFixtureBrain — более строгий actual-game acceptance provider для пяти сценариев. Все участники сценария получают одну factory и общую цель. Решения зависят только от own permitted typed context и public authored map.

- vote: Impostor физически подходит к видимой цели и делает один kill; Crew сообщает о текущем теле, лично видевший нападение может отправить scoped claim, ballots основаны на собственном witness либо реально доставленной claim; без evidence у deadline — Skip.
- parity: Impostor может продолжать реальные видимые kills, Crew пассивно собираются в Hall.
- sabotage: Impostor идет к публично известной room life-support и только после видимого terminal вызывает sabotage; не раскрывает hidden initiator.
- tasks: Crew выбирают реальные own unfinished tasks с устойчивым actor-based offset и учитывают actual completed result; Impostor пассивен.
- privacy: partition по собственной начальной позиции, а не по чужим ролям/позициям; distant Crew уходят в Security, Impostor ждет public delay/own cooldown и делает один видимый kill. Fixture сама не доказывает privacy: нужны независимые probes и совпадение actual kill time с distant interval.

Decode отвергает foreign owner, stale/opaque/truncated/expired/malformed context. Historical witness обязан принадлежать тому же actor/match и быть действительным. Received claim не превращается в verified Alignment. Factories закрывают собственный прогресс; supervisor не передает ни hidden roster, ни target suggestion, ни принудительный результат.

Источник: [social_fixtures.py:1](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_fixtures.py:1), [social_fixtures.py:44](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_fixtures.py:44), [social_fixtures.py:184](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/social_fixtures.py:184).

## Launchers: процессы и доказательство завершения

| Файл | Проверенное поведение |
|---|---|
| `scripts/launch-agent.py` | Development launcher для одного Runtime + ordinary Unity client. Проверяет свободный loopback endpoint, создает свежий environment-only token, передает objective/Brain/model/config только Runtime; Unity получает bridge endpoint и обычные lobby/client flags. Пишет fingerprint credential, а не token. Регистрирует созданный child до обработки сигнала. Cleanup сначала дает Runtime завершить Skills, затем закрывает Unity. |
| `scripts/launch-social-agents.py` | 4–6 независимых numbered launchers, один shared role-neutral objective, отдельные порты/profiles/SQLite/tokens. Public names выбираются OS randomness без игрового RNG. Supervisor читает readiness/isolation/terminal proof, не читает live private roles/positions/claims. |
| `scripts/launch-cooperative-agents.py` | Разные natural-language coordinator/technician objectives; provider каждой стороны scripted или codex. Сначала ждет readiness technician, затем стартует coordinator; данные задания между ними не копирует. |
| `scripts/launch-investigation-agents.py` | Аналогичная отдельная Engineer/Sheriff пара; Sheriff запускается первым до readiness. Общее дело согласуется только по own terminal evidence. |
| `scripts/launch-scripted-agent.py` | Исторический route launcher для `actor-runtime scripted agents-arena`, до Brain-based сценариев. Держит ordinary client открытым после маршрута. Не следует приписывать ему все новые supervisor guarantees. |
| `runtime/actor_agents_arena/launcher.py` | Installed game profile для generic actorctl. Проверяет game-specific public name, выбирает matching 0.10.0 client candidates и flags. Отличается от developer script с фиксированным `build/runtime-venv/bin/actor-runtime`. |

Social completion требует для каждого участника agreement Runtime authoritative_terminal и настоящего client GameOver/summary: один match, winner/reason, actor ↔ accepted session, разные identities. FinishObjective одного агента не означает завершение матча. Frozen own completion сохраняется до shutdown, потому что despawn может очистить current client fields; snapshot не восстанавливается задним числом из одного passed flag. Supervisor отдельно проверяет разные launcher/runtime/client PIDs, ports, profiles, базы и hashes, затем cleanup только своих process groups.

Cooperation acceptance требует оба Objective success и тот же actual Completed task у разных actors/sessions. Investigation требует оба Objective success, тот же incident, distinct duties, Resolved + Restored + validated report. Frozen completion применим только при строго очищенном после despawn current own state и точной принятой identity; не перекрывает failed Objective или другое новое дело.

Источники: [launch-agent.py:56](/Users/fpetrov/PycharmProjects/Actor/scripts/launch-agent.py:56), [launch-agent.py:111](/Users/fpetrov/PycharmProjects/Actor/scripts/launch-agent.py:111), [launch-social-agents.py:112](/Users/fpetrov/PycharmProjects/Actor/scripts/launch-social-agents.py:112), [launch-social-agents.py:158](/Users/fpetrov/PycharmProjects/Actor/scripts/launch-social-agents.py:158), [launch-social-agents.py:200](/Users/fpetrov/PycharmProjects/Actor/scripts/launch-social-agents.py:200), [launch-social-agents.py:334](/Users/fpetrov/PycharmProjects/Actor/scripts/launch-social-agents.py:334), [launch-cooperative-agents.py:47](/Users/fpetrov/PycharmProjects/Actor/scripts/launch-cooperative-agents.py:47), [launch-investigation-agents.py:33](/Users/fpetrov/PycharmProjects/Actor/scripts/launch-investigation-agents.py:33), [launcher.py:7](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_agents_arena/launcher.py:7).

## Spikes и evidence: что считается доказательством

В проекте явно разведены unit/fixture correctness, actual server/client integration, real model turns, rendered/native Human checks, privacy, faults и deployment. Наличие helper не означает, что соответствующий live case запускался или прошел. Исторические version-bound отчеты описывают свое состояние; например multi-agent doc еще содержит Game 0.8.0/Runtime 0.6.0, а текущий installed game profile — Game 0.10.0. Из старого README нельзя выводить текущий rollout.

| Зона | Практика и границы |
|---|---|
| `spikes/grpc` | Ранний transport smoke; отдельные Python/Unity selftests. Не model/game intelligence. В этом дополнении только inventory; исходники не перечитывались. |
| `spikes/codex`, `spikes/jev` | Provider structured output, interruption/runtime safety, SDK contract probes; offline transport и public API отдельно от живой accuracy. Здесь только inventory, ранее детали Jev описаны в основном classifier report. |
| `spikes/cooperation`, `spikes/investigation` | Матрицы сценариев и faults над реальными установленными процедурами; не второй путь выдачи game truth. Здесь inventory, docs и relevant Runtime source. |
| `spikes/social-protocol` | Обычный owned RPC negative probe: wrong match, expiry, stale phase, self-target, replay/correlation conflict, повторный ballot, work cancel race. Strict PASS требует все восемь фактических случаев; отсутствие фазы/предпосылки остается incomplete/fail. Optional plugin не production wheel. |
| `spikes/social-privacy` | Passive actual NGO incoming/adapter probes + authenticated raw gRPC wire auditor до RuntimeSession.accept. Проверяет отсутствие forbidden/foreign facts, не сериализует payload. Для строгого PASS нужен actual server postgame kill внутри одного common stable far-room interval на всех трех поверхностях. |
| `spikes/social-privacy --probe-out-of-sight-kill` | Отдельный active negative request, а не passive observation. Только виденный раньше ID, отсутствующий из свежего view; нужен actual authoritative rejection и matching temporal coverage, не local acceptance. |
| `spikes/social-faults` | Потеря только owned live child, привязка к текущему launch; supervisor не выбирает по hidden Alignment. Клиент/Runtime/server fault — разные доказательства. Сигнал процессу сам по себе не доказывает stopped movement, cleanup или победу. |
| `server_guard.py` | Exact staging-only process proof: UID, executable, PID/start ticks, parent, namespace, sole UDP, allocation, READY, свежий own observer; pidfd сигнал с повторными проверками. Нет numeric-PID fallback/kill by name/production path. SIGTERM не считается abrupt crash. Здесь прочитан contract doc, не вся реализация. |
| `spikes/social-resume` | Drop opaque UDP в обе стороны, actual disconnect, same-process authenticated resume того же actor/match, higher generation, hydration и rejection старого intent, stopped motion, cleanup. Это не restart процесса, resurrection, native GUI или model strategy. |
| `spikes/social-threat` | Optional transparent Codex wrapper: фиксирует только безопасную ordering-связь permitted fresh facts → actual provider choice → executed/server-confirmed reaction. Не меняет prompt, решение или provider. Selected-only не равно executed; local cancellation и server-confirmed phase action разделены. |
| `spikes/native-human` | Обычные deterministic peers для ручного Human UI оператора. Public display name — visible cue, не oracle control mode или role. Helper не подменяет native keyboard/frame/audio proof. |
| `spikes/sandbox-regression` | Fresh Sandbox, unchanged movement/cooperation/investigation launchers; plan-only без execute. Movement input synthetic/headless, не native rendered latency. |
| `spikes/release-validation/run_acceptance.py` | Version-bound wrapper над existing helpers, plan-only default. Execute проверяет fresh exact empty staging room, versions/mode/UDP, allowlist forwarded options; не создает комнату и не меняет server config. |
| `spikes/social-validation/evaluate.py` | Offline consistency join Runtime, client, completion, process/isolation/cleanup и server postgame replay. Не запускает модель/игру и не является подписью против malicious local writer. |
| `spikes/social-validation/performance.py` | Различает protobuf payload, header-only UDP packet size, Runtime usage и headless loop rate. Payload metric не полный wire bandwidth; headless meanFps не display FPS. В этом дополнении прочитаны scope/formulas headers/signatures, не весь parser. |
| `spikes/release-validation/series.py` | Полный denominator attempts: категории, все неудачи, unique match paths, actual provider start отдельно от Exploration/GameOver, social-cycle evidence отдельно от mere win. Missing usage остается unknown; реальные provider costs не вычисляются по winner/tokens. |

Особенно важный принцип series: отсутствие chat text в public artifacts правильно, но aggregate received messages недостаточно, чтобы доказать разговор двух моделей. Full social-cycle gate требует exact postgame model-selected/executed actions и server replay; deception остается unassessed без отдельного sanitized manual review. Все попытки, включая setup/model/runtime failure, должны быть зарегистрированы. Дубликаты paths/matches не увеличивают число запусков. Copied evaluation passed:true не заменяет повторную проверку underlying artifacts.

Raw logs, SQLite, private launch metadata, identities, prompts, messages, observations и hidden reasoning не должны попадать в public evidence. Summarizers формируют allowlisted aggregates. Структурный SQLite redaction audit читает read-only transaction с WAL, проверяет явную private policy и secret-named keys, но не доказывает отсутствие любого перефразированного секрета. Recorded PIDs в offline summary не равны свежей live process verification.

Источники: [summarize-cooperation.py:100](/Users/fpetrov/PycharmProjects/Actor/scripts/summarize-cooperation.py:100), [summarize-cooperation.py:310](/Users/fpetrov/PycharmProjects/Actor/scripts/summarize-cooperation.py:310), [summarize-investigation.py:42](/Users/fpetrov/PycharmProjects/Actor/scripts/summarize-investigation.py:42), [summarize-investigation.py:139](/Users/fpetrov/PycharmProjects/Actor/scripts/summarize-investigation.py:139), [social-privacy/runtime_audit.py:46](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/runtime_audit.py:46), [social-validation/evaluate.py:76](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/evaluate.py:76), [social-validation/evaluate.py:198](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/evaluate.py:198), [run_acceptance.py:71](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/run_acceptance.py:71), [series.py:71](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/series.py:71), [series.py:311](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/series.py:311).

## Исторические результаты не переименованы в текущую приемку

Прочитан вводный accepted status M5: четыре cooperative scenarios (scripted, оба mixed, два независимых Codex), same task/Completed, плюс third Human privacy/chat и faults, в своей исторической версии Game 0.6.0/Runtime 0.4.0. M6 вводный status сообщает шесть accepted investigations (два scripted, оба mixed, два real Codex) и сохраняет failures/ограничения. Эти результаты подтверждают развитие вертикальных срезов; они не доказывают текущую модель MiniLM, текущий Broadcast или произвольное social reasoning.

M6 прямо ограничен одним incident на lobby, без durable active-case restore, shared cognition и cross-lobby evidence archive. Правильный cited report — проверяемый игровой исход ограниченного сценария. Private delivery не гарантирует, что получатель потом сам ничего не расскажет. Native Human UI и controlled rendered performance под двумя Codex в том milestone не доказаны.

Источники: [cooperative-validation/README.md:1](/Users/fpetrov/PycharmProjects/Actor/docs/cooperative-validation/README.md:1), [investigation-validation/README.md:1](/Users/fpetrov/PycharmProjects/Actor/docs/investigation-validation/README.md:1), [investigation-validation/README.md:654](/Users/fpetrov/PycharmProjects/Actor/docs/investigation-validation/README.md:654).

## Инвентарь тестов и честный охват

Тесты не запускались. Ниже в приложении перечислены найденные test functions и их исходные строки; это inventory структуры Python AST, а не число pytest cases и не pass rate. Parametrize/subtests могут давать другое количество исполненных случаев. Содержимое assertions всех этих tests построчно не перечитывалось; назначение сверялось по test names, production source и документации. Отдельные реальные assertions MiniLM integration и classifier tests прочитаны в предыдущем отчете.

Тематическое покрытие:

- calibration: role/room preflight, actual result+assignment, sensitive diagnostic, terminal cancellation и cleanup при живом bridge;
- communication: local acceptance против ServerAccepted/ClientReceived, ранние сообщения, фильтрация sender/task/channel/correlation, expiry/bounds, privacy и отсутствие message-keyword replan;
- investigation: два источника и paired hard negatives, private provenance/expiry, cited report/no-solo semantics, actual repair completion, early message retention и finalizer races;
- navigation/pursuit: четыре fresh samples, cached-state rejection, authored graph/backtracking, limited reroutes/connectors, stopped cleanup, current sight против fixed last seen;
- social runtime: catalog role/phase/life, recipient scope, current replacement bundles, accepted work против completion, phase/death events до frame, postgame gating;
- cognition: provenance/citation groups, unresolved contradictions, immutable source versions, bounded context, discussion batching/deadline reserve, own task prompts/receipts, connection generation fences;
- fixtures: own readiness/roles only, same visible target, claims scope, no hidden tracking, task completion, privacy partition, safe reports;
- launch/summarize: independent endpoints/credentials/databases/processes, valid exact terminal proof, no Finish-only success, frozen snapshots после cleanup, no private export, owned child registration/cleanup.

Полностью прочитаны все 18 Python modules в `runtime/actor_agents_arena` и пять development launch scripts. Layout JSON разобран по структуре и количеству списков. Docs cooperative-tasks, investigation, social-reasoning и multi-agent прочитаны; agent-onboarding и skills — релевантные части (не весь текст skills). Historical acceptance READMEs — вводный статус/ограничения и оглавления, не все сотни строк evidence. Сами сотни исторических JSON не перечитывались.

Для summarize-cooperation прочитаны privacy/numeric/report helpers, completed/result projection, final acceptance/CLI; внутренние строки 141–259 сверены по signatures/uses, не весь текст. Summarize-investigation прочитан полностью. Social evaluate прочитан за исключением отдельного threat_proof helper body; его контракт прочитан в social-threat README. Run_acceptance прочитан полностью. Series прочитан по provider completeness и aggregate/denominator gates плюс полному README; отдельные intermediate project/cycle helpers только по names/contract. WireAuditor прочитан до начала far interval evaluation включительно; remainder проверен по документированному контракту, не весь implementation.

README fully прочитаны: social-protocol, social-privacy, social-resume, social-threat, server-guard, sandbox-regression, native-human, release series. Social-faults и release-validation README — scenario/preflight/limits sections; несколько больших соединенных выводов были усечены, поэтому полного чтения этих двух документов не заявляю. Остальные spike implementations и shell build/deploy scripts в этом дополнении только проинвентаризированы, без claim полного source review.

Это дополнение расширяет исследование Runtime/game semantics и evidence, но не заменяет полный аудит C#/Unity/server/web/CI всей команды.

## Приложение: найденные test functions

Автоматически извлечены из исходного AST; модули не импортировались, функции не исполнялись. Названия служат указателями на проверяемый контракт.

### runtime/tests/test_calibration_events.py — 3 functions

- [test_partner_task_terminal_event_preempts_generic_message_wait](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_calibration_events.py:30)
- [test_unrelated_terminal_task_never_forces_message_wait_replan](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_calibration_events.py:54)
- [test_completed_event_does_not_preempt_successful_calibration_wait](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_calibration_events.py:71)

### runtime/tests/test_calibration_skills.py — 7 functions

- [test_start_requires_server_outcome_and_matching_assignment](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_calibration_skills.py:74)
- [test_server_rejection_is_a_structured_skill_failure](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_calibration_skills.py:95)
- [test_inspection_checks_assignment_and_room_before_any_command](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_calibration_skills.py:110)
- [test_early_diagnostic_result_stays_private_after_success](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_calibration_skills.py:117)
- [test_wait_requires_completed_and_disconnect_cancels_without_hanging](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_calibration_skills.py:138)
- [test_cancelled_task_does_not_become_success](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_calibration_skills.py:156)
- [test_objective_cancel_releases_task_even_when_bridge_stays_connected](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_calibration_skills.py:162)

### runtime/tests/test_codex_launch.py — 3 functions

- [test_actual_final_child_drops_inherited_secrets_preserves_parent_and_exact_argv](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_codex_launch.py:30)
- [test_foreign_or_malformed_launch_refused_before_bundle_resolution](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_codex_launch.py:67)
- [test_dependency_version_mismatch_refuses_even_available_binary](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_codex_launch.py:75)

### runtime/tests/test_communication_skills.py — 14 functions

- [test_send_waits_for_authoritative_acceptance_not_local_command_result](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:61)
- [test_authoritative_rejection_and_actual_client_receipt_are_distinct](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:83)
- [test_local_acceptance_without_server_receipt_times_out](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:99)
- [test_messages_arriving_before_waiter_keep_order_and_do_not_duplicate](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:108)
- [test_wait_filters_sender_task_channel_correlation_and_keeps_other_messages](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:123)
- [test_message_wait_terminal_paths_release_foreground](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:136)
- [test_invisible_old_expired_or_oversized_messages_never_reach_world_or_context](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:150)
- [test_expiry_and_bounded_inbox_are_observable](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:159)
- [test_ordinary_chat_flood_and_schema_words_never_enter_global_secret_registry](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:172)
- [test_expected_task_message_wins_context_budget_over_unrelated_later_messages](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:184)
- [test_descriptor_only_communication_payload_is_consumed_by_generated_typed_wait](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:201)
- [test_private_payloads_expire_from_context_but_stay_redacted_from_reports](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:222)
- [test_communication_code_reaches_only_working_brain_never_sqlite_reports_or_logs](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:238)
- [test_invalid_outbound_message_never_issues_command](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_communication_skills.py:275)

### runtime/tests/test_investigation_brains.py — 10 functions

- [test_correlation_requires_matching_confirmed_action_not_visit_or_revision_delta](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_brains.py:33)
- [test_missing_or_ambiguous_joint_support_is_not_a_conclusive_mechanical_report](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_brains.py:46)
- [test_manual_report_also_requires_complete_fault_window_coverage](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_brains.py:57)
- [test_counterfactual_pairs_require_both_views_instead_of_a_mechanical_handshake](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_brains.py:64)
- [test_scripted_sheriff_uses_received_diagnostic_and_retained_own_security](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_brains.py:108)
- [test_unrelated_or_unsubstantiated_claim_never_becomes_a_report](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_brains.py:129)
- [test_engineer_uses_same_skill_pipeline_and_never_proposes_hidden_origin](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_brains.py:144)
- [test_failed_or_preempted_procedure_cannot_false_finish_successfully](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_brains.py:159)
- [test_public_operator_never_reads_private_views_or_active_circuit](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_brains.py:168)
- [test_plugins_are_discovered_without_core_game_branch](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_brains.py:187)

### runtime/tests/test_investigation_events.py — 5 functions

- [test_own_terminal_incident_preempts_generic_message_wait_without_core_branch](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_events.py:28)
- [test_foreign_or_stale_incident_loss_does_not_force_review](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_events.py:44)
- [test_started_event_does_not_cancel_expected_wait_for_incident](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_events.py:56)
- [test_incident_started_preserves_pending_real_maintenance_receipt](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_events.py:73)
- [test_resolved_event_does_not_preempt_the_successful_final_wait](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_events.py:85)

### runtime/tests/test_investigation_skills.py — 23 functions

- [test_other_role_cannot_start_private_procedure](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:129)
- [test_select_role_requires_receipt_and_authoritative_own_state](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:136)
- [test_private_inspection_retains_own_evidence_across_turns_and_reinspection](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:162)
- [test_private_view_rejects_foreign_role_recipient_and_clears_old_knowledge](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:183)
- [test_expired_own_evidence_cannot_start_repair_or_remain_in_context](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:202)
- [test_repair_needs_completed_same_correlation_phase_and_restored_observation](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:214)
- [test_repair_cancellation_sends_typed_cancel_and_zero_before_lease_release](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:235)
- [test_repair_rejection_never_claims_success_or_cancels_another_accepted_operation](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:249)
- [test_share_uses_actual_direct_transport_receipt_and_bounded_claim](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:262)
- [test_sharing_nearly_expired_evidence_does_not_extend_it_to_minimum_message_ttl](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:288)
- [test_report_schema_requires_exactly_three_distinct_references](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:297)
- [test_report_cites_own_security_and_one_record_and_awaits_server_validation](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:304)
- [test_local_report_shape_does_not_override_a_server_rejection](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:323)
- [test_early_peer_message_survives_security_inspection_and_never_becomes_own_fact](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:335)
- [test_resolution_requires_repair_and_validated_report](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:347)
- [test_maintenance_waits_for_distinct_duties_and_real_authoritative_receipt](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:356)
- [test_objective_cancel_releases_investigation_with_bridge_still_open](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:381)
- [test_pre_alarm_unsuccessful_objective_releases_selected_duty_without_closing_bridge](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:405)
- [test_successful_role_only_objective_preserves_selected_duty_and_pending_cycle](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:432)
- [test_incident_start_during_pre_alarm_release_is_cancelled_using_its_authoritative_identity](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:444)
- [test_private_inspection_results_and_events_are_omitted_from_sqlite](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:478)
- [test_maintenance_requests_public_operation_only_after_right_lane_service_access](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:494)
- [test_private_inspection_uses_own_service_access_before_typed_request](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_investigation_skills.py:525)

### runtime/tests/test_launch_process_identity.py — 4 functions

- [test_full_identity_stays_equal_across_distinct_caller_widths_locales_and_ps_environments](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_launch_process_identity.py:42)
- [test_stable_format_does_not_weaken_actual_pid_start_or_full_command_mismatch](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_launch_process_identity.py:62)
- [test_failed_or_malformed_metadata_query_fails_closed_without_retry](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_launch_process_identity.py:77)
- [test_invalid_pid_never_queries_ps](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_launch_process_identity.py:97)

### runtime/tests/test_launcher.py — 1 functions

- [test_sigterm_launcher_stops_owned_children_and_allows_provider_cleanup](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_launcher.py:37)

### runtime/tests/test_m8_cognition.py — 17 functions

- [test_contradiction_is_uncertain_model_note_with_two_actual_sources_and_expiry](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:34)
- [test_context_budget_keeps_note_and_evidence_as_a_group_and_never_changes_time](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:51)
- [test_notes_pin_exact_observed_version_while_latest_source_and_claim_advance](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:65)
- [test_replacing_a_mutable_source_invalidates_direct_dependent_note_cascade](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:83)
- [test_full_knowledge_store_installs_exact_citation_group_atomically](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:97)
- [test_citation_group_that_cannot_fit_fails_without_partial_snapshots](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:111)
- [test_same_fact_via_latest_and_snapshot_alias_cannot_form_a_contradiction](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:120)
- [test_phase_selection_reduces_external_bytes_without_synthesizing_facts](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:131)
- [test_early_discussion_burst_returns_once_as_attributed_claims_without_replan](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:164)
- [test_discuss_requires_real_receipt_then_batches_without_claiming_read](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:176)
- [test_displayed_task_step_and_final_original_receipt_are_both_required](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:201)
- [test_connection_generation_changes_episode_even_without_phase_change](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:223)
- [test_resume_event_blocks_reasoning_until_new_generation_observation](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:230)
- [test_compact_provider_catalog_deduplicates_exact_schemas_without_changing_validation](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:242)
- [test_new_social_capability_negotiates_into_typed_skill_with_unchanged_core](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:257)
- [test_task_completion_budget_covers_public_timed_steps_instead_of_hold_duration](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:297)
- [test_task_step_waits_for_own_sample_window_sends_once_and_fences_resume](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_cognition.py:309)

### runtime/tests/test_m8_pursuit.py — 6 functions

- [test_moving_visible_actor_reached_within_public_range_and_four_distinct_stops](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_pursuit.py:50)
- [test_vertical_moving_capsule_contact_can_reach_a_real_kill_range_opportunity](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_pursuit.py:58)
- [test_lost_target_uses_only_fixed_last_delivered_position_then_stops](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_pursuit.py:68)
- [test_cached_stop_cannot_be_counted_as_four_new_authoritative_samples](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_pursuit.py:79)
- [test_visible_peer_changes_local_direction_and_public_floor_constrains_it](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_pursuit.py:86)
- [test_foreground_follow_cancellation_zeroes_intent_and_stops_command_count](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_m8_pursuit.py:98)

### runtime/tests/test_navigation_skill.py — 15 functions

- [test_missing_and_stale_observations_fail_before_skill_start](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:43)
- [test_unknown_target_is_typed_failure_without_movement](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:56)
- [test_one_cached_stopped_sample_cannot_complete_navigation](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:62)
- [test_new_room_samples_do_not_refresh_cached_movement_or_prove_stopped](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:70)
- [test_nonfinite_movement_state_fails_preflight](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:83)
- [test_midroute_stale_state_and_cancel_always_zero](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:89)
- [test_navigation_round_trip_uses_real_typed_session_and_authoritative_room_events](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:107)
- [test_service_access_bypasses_occupied_hall_and_engineering_anchors](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:244)
- [test_sheriff_service_return_changes_lane_above_waiting_engineering_capsule](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:259)
- [test_service_same_room_still_reaches_its_actual_panel_approach](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:275)
- [test_service_cross_room_membership_without_transition_event_cannot_succeed](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:289)
- [test_service_cached_stop_and_cancel_cannot_leak_movement_or_false_arrival](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:301)
- [test_service_tight_stop_converges_with_150ms_authoritative_delay_at_actual_speed](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:315)
- [test_two_live_legacy_inspectors_separate_security_access_from_engineering_stem](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:387)
- [test_same_hall_security_shelf_clears_occupied_anchor_and_real_floor_from_opposite_side](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_navigation_skill.py:411)

### runtime/tests/test_plans.py — 14 functions

- [test_sequential_plan_calls_brain_only_before_and_after_the_plan](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:91)
- [test_failed_step_runs_one_fallback_and_discards_remaining_steps](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:104)
- [test_matching_observation_runs_planned_response_after_cleanup_without_a_brain_call](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:115)
- [test_each_plan_reassesses_the_same_fresh_observation_once](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:147)
- [test_descriptor_only_negotiated_type_can_supply_a_plan_condition](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:194)
- [test_late_observation_assessment_cannot_replan_the_next_accepted_plan](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:242)
- [test_unresolvable_condition_type_is_rejected_before_any_skill](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:287)
- [test_deferred_observation_review_cannot_replan_the_next_accepted_plan](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:300)
- [test_neural_request_for_review_supersedes_the_preselected_response](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:339)
- [test_authoritative_phase_change_invalidates_the_entire_plan](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:356)
- [test_invalid_condition_rejects_the_whole_plan_before_any_action](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:376)
- [test_phase_change_during_planned_cleanup_discards_the_queued_response](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:387)
- [test_other_actor_observation_cannot_activate_a_plan](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:414)
- [test_failed_cleanup_stops_plan_before_next_step_or_reaction](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_plans.py:432)

### runtime/tests/test_protocol_communication.py — 11 functions

- [test_generated_and_descriptor_only_communication_any_roundtrip](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:60)
- [test_communication_is_independent_of_motion_core_and_arena](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:72)
- [test_communication_optional_missing_or_incompatible_does_not_require_game_upgrade](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:87)
- [test_manifest_metadata_has_four_typed_capabilities_and_ordered_message_delivery](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:100)
- [test_motion_fingerprint_and_core_message_structure_remain_unchanged](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:113)
- [test_server_identity_and_game_outcome_are_not_client_claim_fields](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:125)
- [test_calibration_public_state_never_contains_diagnostic_secret_fields](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:133)
- [test_foreign_generated_types_cannot_be_advertised_under_communication_hash](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:151)
- [test_fixture_advertises_communication_optionally_and_existing_game_packages_required](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:156)
- [test_game_without_communication_negotiates_normally_even_when_plugin_is_installed](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:162)
- [test_optional_communication_unsupported_runtime_keeps_opaque_metadata_without_breaking_game](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_protocol_communication.py:180)

### runtime/tests/test_skills.py — 16 functions

- [test_synthetic_skill_plugin_discovery_without_core_branch](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:89)
- [test_duplicates_and_conflicting_versions_reject](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:95)
- [test_invalid_model_schema_and_undeclared_command_ownership_reject](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:104)
- [test_catalog_deterministic_and_excludes_unavailable_capability](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:114)
- [test_invalid_typed_arguments_never_reach_implementation](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:128)
- [test_wait_arguments_finite_strict_and_bounded](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:139)
- [test_wait_monotonic_duration_and_generic_success](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:145)
- [test_all_execution_paths_cleanup_zero_and_revoke_rights](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:156)
- [test_cancel_waits_cleanup_and_releases_foreground](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:169)
- [test_direct_run_task_cancellation_returns_controlled_result_after_cleanup](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:182)
- [test_cancel_before_execute_begins_cannot_leak_foreground_lease](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:193)
- [test_foreground_ownership_shared_across_schedulers](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:204)
- [test_session_disconnect_fails_and_cleanup_executes](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:214)
- [test_prerequisite_failure_does_not_start_skill_or_issue_commands](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:225)
- [test_command_owner_without_explicit_cleanup_cannot_start](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:235)
- [test_invalid_lifecycle_signature_cannot_start_or_leak_foreground](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_skills.py:247)

### runtime/tests/test_social_fixtures.py — 22 functions

- [test_every_factory_marks_only_own_readiness_without_assigning_alignment](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:88)
- [test_only_authoritative_gameover_finishes_an_episode](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:96)
- [test_kill_approach_keeps_the_exact_visible_target_when_list_order_changes](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:103)
- [test_kill_never_tracks_lost_target_or_uses_historical_position](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:114)
- [test_own_kill_cooldown_is_observed_before_approach_or_kill](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:123)
- [test_vote_stops_after_one_kill_but_parity_can_continue_with_visible_survivors](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:130)
- [test_new_vote_episode_remembers_only_personally_observed_body_not_player_claim](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:139)
- [test_crew_vote_reports_current_body_while_parity_crew_remain_idle](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:152)
- [test_personally_witnessed_attack_can_be_communicated_once_during_discussion](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:163)
- [test_untrusted_or_foreign_record_cannot_become_personal_witness](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:174)
- [test_foreign_observer_event_cannot_be_relabelled_as_own_attack](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:180)
- [test_crew_vote_uses_personal_evidence_or_actual_delivered_claim_and_impostor_skips](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:187)
- [test_no_evidence_waits_for_claims_then_explicitly_skips_near_deadline](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:201)
- [test_claim_scope_recipient_channel_expiry_and_sender_are_checked](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:211)
- [test_chat_prose_and_instruction_payloads_do_not_supply_verified_actions](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:222)
- [test_sabotage_uses_public_authored_navigation_then_actual_visible_terminal_skill](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:231)
- [test_tasks_choose_stable_actor_based_work_and_require_actual_completed_result](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:244)
- [test_task_selection_uses_updated_authoritative_completion_after_episode_restart](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:258)
- [test_privacy_partition_uses_only_own_spawn_pose_and_remains_far_after_episode_restart](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:265)
- [test_privacy_impostor_waits_for_departure_window_then_kills_once](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:276)
- [test_factories_have_independent_private_progress_and_safe_aggregate_reports](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:296)
- [test_wrong_recipient_stale_or_malformed_typed_world_cannot_supply_a_target](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_fixtures.py:306)

### runtime/tests/test_social_navigation.py — 14 functions

- [test_blocked_hall_stem_uses_actual_public_east_loop_after_backtracking](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:139)
- [test_single_engineering_branch_cannot_invent_a_route_past_a_blockage](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:154)
- [test_blocked_edge_is_bidirectional_and_cannot_be_reintroduced_by_nearest_node](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:165)
- [test_two_blocked_alternatives_fail_instead_of_repeating_the_same_edge](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:174)
- [test_maximum_two_reroutes_bounds_three_distinct_blocked_alternatives](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:185)
- [test_authoritative_arrival_requires_room_transition_event_even_after_detour](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:203)
- [test_cached_stop_sample_cannot_satisfy_anchor_arrival](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:211)
- [test_nonfinite_own_pose_fails_before_any_nonzero_intent](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:218)
- [test_cancellation_during_blocked_travel_clears_intent](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:226)
- [test_same_room_service_approach_is_reached_and_stopped](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:237)
- [test_initial_meeting_anchor_contact_tries_distinct_own_room_nodes_without_edge_ban](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:245)
- [test_two_live_contacting_crew_escape_initial_anchor_in_closed_loop](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:266)
- [test_initial_connector_budget_is_three_physically_attempted_anchors_without_false_graph_progress](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:284)
- [test_initial_connector_identity_stagger_is_deterministic_and_cancellation_stops](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_navigation.py:301)

### runtime/tests/test_social_plans.py — 5 functions

- [test_body_handled_by_an_accepted_plan_does_not_force_another_episode](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_plans.py:18)
- [test_a_plan_without_a_body_condition_keeps_ordinary_discovery_behavior](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_plans.py:31)
- [test_report_visible_body_uses_real_visible_target_and_correlated_server_completion](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_plans.py:41)
- [test_report_visible_body_without_a_current_body_only_stops_movement](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_plans.py:52)
- [test_future_plan_is_private_in_public_diagnostics](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_plans.py:61)

### runtime/tests/test_social_player_prompt.py — 2 functions

- [test_installed_player_guidance_reaches_codex_without_rewriting_chat_or_promoting_claims](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_player_prompt.py:53)
- [test_full_length_user_objective_keeps_precedence_over_player_style_guidance](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_player_prompt.py:94)

### runtime/tests/test_social_runtime.py — 13 functions

- [test_role_phase_and_life_filter_catalog_and_reject_direct_calls](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:81)
- [test_current_visible_bundle_replaces_hidden_actor_and_history_does_not_refresh](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:99)
- [test_own_alignment_and_world_are_session_only_and_foreign_target_is_rejected](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:114)
- [test_kill_requires_current_visibility_and_authoritative_correlated_outcome](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:125)
- [test_work_acceptance_is_not_completion_and_cancel_uses_own_original_correlation](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:144)
- [test_received_claims_remain_private_unverified_across_episodes](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:167)
- [test_new_body_changes_episode_key_but_ordinary_actor_motion_does_not](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:182)
- [test_own_death_and_game_over_event_invalidate_execution_before_next_frame](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:195)
- [test_phase_transition_event_stops_old_catalog_until_coherent_frame](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:211)
- [test_postgame_summary_cannot_arrive_before_terminal_but_can_follow_event_before_frame](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:222)
- [test_only_validated_postgame_summary_becomes_durable_public_evidence](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:238)
- [test_scripted_roles_use_only_own_state_and_actual_claim_context](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:264)
- [test_visible_approach_stops_inside_default_kill_range_without_impossible_vertical_overlap](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_social_runtime.py:278)

### scripts/tests/test_launch_cooperative.py — 2 functions

- [test_headless_switch_reaches_both_isolated_launchers_only_when_requested](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_cooperative.py:17)
- [test_help_accepts_flag_without_starting_runtime_or_unity](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_cooperative.py:41)

### scripts/tests/test_launch_registration.py — 2 functions

- [test_single_agent_runtime_and_client_registered_before_signal_or_log_close](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_registration.py:122)
- [test_social_supervisor_group_registered_before_signal_or_log_close](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_registration.py:129)

### scripts/tests/test_launch_social.py — 27 functions

- [test_explicit_audit_provider_and_policy_do_not_assign_roles](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:39)
- [test_policy_registry_name_is_bounded_before_process_creation](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:51)
- [test_all_independent_participants_receive_same_role_agnostic_objective](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:58)
- [test_provider_choices_and_windowed_setting_do_not_assign_hidden_roles](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:78)
- [test_common_objective_override_is_identical_for_all_without_role_assignment](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:89)
- [test_russian_name_pool_is_short_unique_and_shared_by_standalone_defaults](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:105)
- [test_name_sampling_is_once_without_replacement_and_preserves_internal_identity](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:113)
- [test_explicit_public_names_are_preserved_without_consuming_randomness](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:131)
- [test_invalid_name_overrides_fail_before_any_owned_process](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:146)
- [test_existing_process_manifest_retains_resolved_names_before_child_reports](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:157)
- [test_objective_default_is_unchanged_and_maximum_length_is_supported](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:166)
- [test_invalid_common_objectives_fail_before_any_process_is_spawned](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:175)
- [test_default_is_headless_and_explicit_windowed_override_is_supported](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:182)
- [test_invalid_ports_provider_count_timeout_and_room_fail_before_processes](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:189)
- [test_help_never_requires_unity_runtime_or_credentials](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:196)
- [test_single_agent_launcher_preserves_agent_default_and_match_passthrough](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:203)
- [test_snapshot_requires_authoritative_runtime_and_actual_client_gameover](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:210)
- [test_finishobjective_alone_never_claims_match_completion](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:225)
- [test_identity_match_phase_and_winner_must_agree](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:229)
- [test_terminal_snapshot_is_safe_whitelist_without_alignment_chat_or_context](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:239)
- [test_distinct_actors_sessions_and_same_match_are_required_for_all_players](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:251)
- [test_corrupt_reports_are_bounded_and_cannot_complete_a_match](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:262)
- [test_pid_credential_profile_and_database_isolation_are_measured_from_child_metadata](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:272)
- [test_api_override_is_validated_and_passed_to_each_ordinary_child](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:287)
- [test_partial_spawn_failure_still_cleans_only_created_owned_launchers](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:298)
- [test_output_write_failure_cannot_skip_owned_process_cleanup](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:308)
- [test_deterministic_fixture_providers_are_explicitly_available_without_role_hints](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_launch_social.py:316)

### scripts/tests/test_summarize_cooperation.py — 21 functions

- [test_two_authoritative_completions_and_objectives_pass](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:74)
- [test_replan_count_preserves_numeric_measurement_and_rejects_bad_types](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:82)
- [test_finish_objective_without_server_completion_never_passes](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:91)
- [test_completed_barrier_snapshot_preserves_proof_after_exact_owner_cleanup](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:97)
- [test_independent_own_success_is_latched_before_partner_finishes](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:112)
- [test_snapshot_capture_selects_only_public_barrier_fields](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:128)
- [test_barrier_rejects_missing_own_assignment_failed_different_task_or_shared_identity](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:136)
- [test_missing_historical_snapshot_does_not_backfill_success_from_objectives](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:148)
- [test_failed_or_new_current_task_cannot_be_overridden_by_old_completed_snapshot](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:155)
- [test_snapshot_identity_session_and_runtime_binding_are_mandatory](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:168)
- [test_malformed_failed_or_private_snapshot_is_rejected](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:182)
- [test_snapshot_never_substitutes_for_failed_objective](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:195)
- [test_snapshot_is_protected_from_summary_output_overwrite](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:200)
- [test_server_completion_without_objective_success_never_passes](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:208)
- [test_wrong_task_or_shared_actor_never_passes](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:212)
- [test_shared_runtime_endpoint_never_claims_independent_runtimes](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:219)
- [test_different_paths_to_same_database_never_pass](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:225)
- [test_no_prose_codes_or_message_content_is_published](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:235)
- [test_unredacted_private_database_payload_fails_without_dumping_it](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:240)
- [test_missing_reports_are_incomplete_and_safe](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:249)
- [test_require_success_exit_code_and_protected_raw_artifacts](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_cooperation.py:255)

### scripts/tests/test_summarize_investigation.py — 24 functions

- [test_strict_authoritative_two_role_completion_passes](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:82)
- [test_actual_provider_metrics_and_registered_skill_ids_are_retained](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:89)
- [test_own_completed_snapshot_survives_despawn_with_exact_accepted_runtime_identity](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:99)
- [test_completion_capture_requires_contemporaneous_own_success_and_resolution](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:113)
- [test_missing_snapshot_does_not_reconstruct_completion_after_despawn](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:127)
- [test_snapshot_cannot_override_failed_or_new_current_incident](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:133)
- [test_snapshot_rejects_replaced_actor_session_duplicate_runtime_and_wrong_role](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:145)
- [test_snapshot_cannot_restore_final_counts_private_checks_or_failed_objective](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:159)
- [test_snapshot_unknown_private_or_non_success_fields_are_rejected](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:170)
- [test_snapshot_inputs_are_protected_from_output_overwrite](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:183)
- [test_finish_alone_missing_report_or_unrestored_cooling_never_passes](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:190)
- [test_server_resolution_without_objective_success_never_passes](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:197)
- [test_wrong_incident_shared_actor_or_wrong_duty_never_passes](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:201)
- [test_accepted_session_survives_unsuccessful_reconnect](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:207)
- [test_shared_endpoint_credentials_or_processes_fail_closed](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:211)
- [test_same_database_hardlink_and_foreign_profile_fail](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:219)
- [test_private_receiver_counters_and_missing_authoritative_work_fail](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:227)
- [test_private_content_hidden_origin_session_ids_paths_and_fingerprints_are_not_published](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:233)
- [test_unredacted_sqlite_evidence_fails_without_exposing_it](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:238)
- [test_missing_or_untrusted_reports_remain_safe_incomplete](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:244)
- [test_cli_strict_failure_and_protected_private_inputs](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:251)
- [test_plans_isolate_ports_profiles_databases_and_providers](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:263)
- [test_final_barrier_requires_authoritative_both_branch_resolution](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:277)
- [test_nonfinite_timeout_rejected_before_creating_output](/Users/fpetrov/PycharmProjects/Actor/scripts/tests/test_summarize_investigation.py:287)

### spikes/broadcast/test_provider_policy.py — 4 functions

- [test_original_feature_flags_do_not_physically_remove_all_model_tools](/Users/fpetrov/PycharmProjects/Actor/spikes/broadcast/test_provider_policy.py:172)
- [test_inherited_harmless_mcp_exposes_discovery_without_explicit_disable](/Users/fpetrov/PycharmProjects/Actor/spikes/broadcast/test_provider_policy.py:181)
- [test_restricted_policy_physically_emits_no_tool_specs](/Users/fpetrov/PycharmProjects/Actor/spikes/broadcast/test_provider_policy.py:190)
- [test_production_commentary_hydrates_policy_and_streams_external_speech_with_real_sdk](/Users/fpetrov/PycharmProjects/Actor/spikes/broadcast/test_provider_policy.py:198)

### spikes/native-human/test_fixture.py — 12 functions

- [test_ordinary_ready_dead_passive_and_authoritative_terminal](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:77)
- [test_crew_never_automates_task_report_or_meeting](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:86)
- [test_witness_waits_45s_and_excludes_only_public_human_name](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:93)
- [test_lost_visible_target_is_never_killed_from_history](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:102)
- [test_witness_stops_after_first_body_without_auto_reporting](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:109)
- [test_victim_first_targets_visible_public_human_then_second_ai_after_180s](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:118)
- [test_claim_foreign_or_expired_body_cannot_unlock_second_kill](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:135)
- [test_victim_ambiguous_public_name_and_cooldown_fail_closed](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:141)
- [test_discussion_sends_one_neutral_message_per_meeting_and_never_claims_a_witness](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:147)
- [test_vote_crew_requires_own_witness_or_delivered_claim_impostor_skips](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:155)
- [test_spoofed_own_witness_and_unreceived_prose_do_not_supply_vote](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:168)
- [test_participant_count_is_not_assumed_and_report_omits_actor_names_and_ids](/Users/fpetrov/PycharmProjects/Actor/spikes/native-human/test_fixture.py:175)

### spikes/release-validation/test_audit_scene.py — 7 functions

- [test_regenerated_local_ids_do_not_change_graph](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_audit_scene.py:36)
- [test_order_and_insignificant_trailing_space_do_not_change_graph](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_audit_scene.py:40)
- [test_collider_size_transform_and_layer_change_never_pass](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_audit_scene.py:43)
- [test_external_asset_guid_is_preserved](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_audit_scene.py:48)
- [test_ambiguous_component_graph_is_rejected_instead_of_merged](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_audit_scene.py:51)
- [test_cycle_duplicate_anchor_and_empty_are_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_audit_scene.py:54)
- [test_paths_are_relative_contained_and_symlink_safe](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_audit_scene.py:58)

### spikes/release-validation/test_mixed.py — 20 functions

- [test_valid_three_official_models_and_explicit_native_operator_consistency](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:71)
- [test_frozen_human_must_be_fourth_actor_same_match_phase_and_winner](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:81)
- [test_mode_field_does_not_prove_human_input_or_native_window](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:90)
- [test_native_operator_identity_is_bound_to_frozen_human](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:99)
- [test_forged_hash_corrupt_png_and_valid_crc_invalid_pixels_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:105)
- [test_screenshots_must_be_contained_distinct_ordered_and_before_leave_capture](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:118)
- [test_model_must_be_original_official_codex_and_all_provider_counters_real](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:130)
- [test_model_terminal_and_accepted_identity_cannot_be_supervisor_only](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:147)
- [test_different_authoritative_summary_or_missing_fourth_player_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:158)
- [test_cleanup_requires_all_three_groups_and_matching_process_identities](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:165)
- [test_explicit_group_total_cannot_be_overridden_by_contradictory_row_booleans](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:174)
- [test_unknown_tool_usage_identity_and_error_prose_never_reach_public_output](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:186)
- [test_claimed_human_report_or_vote_requires_own_server_replay_not_peer_action](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:192)
- [test_typed_human_report_and_vote_claims_join_exact_fourth_actor_replay](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:212)
- [test_peer_report_and_vote_cannot_substitute_human_actions](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:219)
- [test_failure_and_missing_native_evidence_preserve_failed_category](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:224)
- [test_invented_terminal_replay_counter_or_truncated_history_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:232)
- [test_removing_kills_and_counter_cannot_keep_unsupported_parity_or_dead_players](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:245)
- [test_parity_can_follow_actual_crew_ejections_without_inventing_two_kills](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:260)
- [test_ejection_requires_a_real_unique_winning_ballot_not_just_a_named_victim](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_mixed.py:277)

### spikes/release-validation/test_run_acceptance.py — 6 functions

- [test_exact_public_versions_room_and_mode_allow_only_matching_empty_fresh_target](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_run_acceptance.py:31)
- [test_pass_through_cannot_override_scope_via_duplicates_aliases_or_abbreviations](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_run_acceptance.py:54)
- [test_dispatch_keeps_original_helpers_and_binds_lifecycle_match](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_run_acceptance.py:64)
- [test_plan_never_opens_network_or_executes_and_public_projection_has_no_identity](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_run_acceptance.py:75)
- [test_explicit_execution_refuses_old_version_before_original_helper_can_start](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_run_acceptance.py:86)
- [test_cli_scope_and_output_refusals_never_query_public_api](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_run_acceptance.py:95)

### spikes/release-validation/test_series.py — 26 functions

- [test_valid_independent_codex_start_and_gameover_are_separate_from_full_cycle](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:65)
- [test_full_cycle_requires_actor_bound_server_actions_and_real_multi_sender_discuss](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:72)
- [test_generic_send_message_cannot_substitute_unknown_discussion_channel](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:78)
- [test_production_privacy_collected_before_terminal_cannot_manufacture_discussion_proof](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:89)
- [test_selected_but_failed_discuss_and_missing_receipt_counters_are_not_discussion_proof](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:104)
- [test_zero_actual_received_messages_cannot_pass_full_cycle](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:110)
- [test_cancelled_report_requires_exact_server_body_report_not_generic_meeting](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:114)
- [test_missing_terminal_proof_cannot_be_overruled_by_passed_summary](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:119)
- [test_model_failure_preserves_attempt_usage_and_start_without_gameover](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:123)
- [test_private_failed_episodes_remain_unknown_but_scalar_measured_tokens_survive](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:129)
- [test_future_production_scalar_telemetry_proves_only_bound_sdk_start_not_gameover](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:139)
- [test_scalar_start_requires_complete_closed_pinned_sdk_and_zero_actual_tools](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:149)
- [test_missing_scalar_tool_counts_remain_unknown_not_zero_in_public_totals](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:164)
- [test_setup_failure_without_any_report_is_retained_in_denominator](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:172)
- [test_running_null_participation_is_incomplete_not_invalid_or_a_started_codex_proof](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:184)
- [test_unregistered_attempt_directory_prevents_complete_denominator_claim](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:194)
- [test_duplicate_match_scope_cannot_manufacture_a_series_or_duplicate_usage](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:197)
- [test_no_postgame_role_or_private_fields_reflected_from_failed_runtime](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:202)
- [test_wrong_game_version_or_duplicate_accepted_session_blocks_m8_acceptance](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:209)
- [test_invalid_numeric_usage_is_not_coerced_to_success](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:215)
- [test_invalid_entry_and_outside_path_remain_explicit_attempt_rows](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:218)
- [test_deception_requires_explicit_postgame_sanitized_review_not_impostor_win](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:222)
- [test_cost_never_inferred_but_actual_reported_numeric_cost_can_be_counted](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:230)
- [test_five_distinct_match_starts_are_counted_independently_of_terminal_success](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:234)
- [test_scripted_controlled_and_human_categories_never_fill_main_codex_series](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:246)
- [test_full_cycle_rejects_truncated_replay_even_with_positive_counters](/Users/fpetrov/PycharmProjects/Actor/spikes/release-validation/test_series.py:253)

### spikes/sandbox-regression/test_regression.py — 6 functions

- [test_wrapper_uses_exec_preserves_auth_environment_and_forces_staging_headless_window_flags](/Users/fpetrov/PycharmProjects/Actor/spikes/sandbox-regression/test_regression.py:17)
- [test_default_preparation_never_launches_or_reserves_a_room](/Users/fpetrov/PycharmProjects/Actor/spikes/sandbox-regression/test_regression.py:32)
- [test_legacy_plans_stay_scripted_independent_and_use_the_only_client_wrapper](/Users/fpetrov/PycharmProjects/Actor/spikes/sandbox-regression/test_regression.py:42)
- [test_prediction_requires_all_distinct_real_check_names_and_nonfinite_cannot_pass](/Users/fpetrov/PycharmProjects/Actor/spikes/sandbox-regression/test_regression.py:62)
- [test_objective_success_alone_is_not_an_authoritative_pair](/Users/fpetrov/PycharmProjects/Actor/spikes/sandbox-regression/test_regression.py:72)
- [test_public_prediction_projection_omits_private_ids_positions_and_error_text](/Users/fpetrov/PycharmProjects/Actor/spikes/sandbox-regression/test_regression.py:88)

### spikes/social-faults/test_faults.py — 32 functions

- [test_real_protobuf_json_64_bit_strings_and_default_fields](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:57)
- [test_private_and_arbitrary_prose_is_not_in_projection](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:65)
- [test_duplicate_actor_invalid_enum_and_order_are_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:70)
- [test_crew_loss_uses_actual_parity_and_impostor_loss_crew_victory](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:80)
- [test_missing_actual_fault_and_missing_summary_do_not_pass](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:88)
- [test_wrong_game_summary_or_runtime_scope_and_session_are_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:96)
- [test_unknown_case_cannot_claim_an_actual_pass](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:105)
- [test_fault_and_pre_fault_match_ids_must_bind_to_terminal_match](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:111)
- [test_duplicate_accepted_session_id_is_rejected_even_with_foreign_actor](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:116)
- [test_no_body_claim_requires_disconnect_complete_replay_and_no_observed_body](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:121)
- [test_survivors_complete_but_faulted_runtime_need_not_report_success](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:130)
- [test_runtime_loss_requires_own_unavailable_zero_keyboard_and_live_fps](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:136)
- [test_single_phase_cannot_claim_both_or_other_faults](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:146)
- [test_metadata_isolation_cleanup_and_exact_pid_guard_are_required](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:158)
- [test_public_acceptance_never_copies_nested_reports_or_prose](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:166)
- [test_fresh_stationary_no_alignment_no_commands_is_required](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:184)
- [test_late_reconnect_cannot_pass_without_actual_spectator_proof](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:196)
- [test_reconnect_requires_real_discussion_epoch_time_window_and_session](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:220)
- [test_meeting_proof_rejects_old_unity_report_or_postgame_only_spectator](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:236)
- [test_discussion_evaluation_uses_frozen_phase_proof_not_final_gameover_values](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:242)
- [test_only_real_new_pose_motion_frames_count_towards_stationary_spectator](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:288)
- [test_discussion_samples_reset_on_phase_change_gap_and_require_current_own_and_match](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:299)
- [test_foreign_entity_wrong_canonical_type_and_foreign_own_are_errors](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:316)
- [test_body_victims_are_bounded_and_only_permitted_wire_ids_retained](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:325)
- [test_postgame_requires_terminal_but_same_frame_order_is_irrelevant](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:333)
- [test_nonfinite_spectator_motion_is_not_stationary_evidence](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:345)
- [test_only_exact_owned_live_child_can_be_faulted](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:353)
- [test_wrong_manifest_pid_exited_child_and_unknown_component_are_never_signalled](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:359)
- [test_cleanup_only_holds_own_children_and_requests_runtime_stop_before_client](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:365)
- [test_meeting_reconnect_is_explicit_and_exploration_stays_default](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:377)
- [test_discussion_must_be_explicit_and_url_cannot_contain_credentials](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:383)
- [test_summarize_only_does_not_need_lobby_or_processes](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_faults.py:390)

### spikes/social-faults/test_lifecycle.py — 13 functions

- [test_explicit_cancel_requires_live_bridge_released_lease_and_stable_commands](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:27)
- [test_shutdown_or_session_loss_does_not_impersonate_explicit_objective_cancel](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:37)
- [test_codex_denial_requires_real_turn_metadata_and_original_error_code](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:42)
- [test_server_observer_requires_expiry_and_session_failure_with_separate_fault_proof](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:55)
- [test_absent_execution_unknown_case_and_cleanup_failure_never_pass](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:69)
- [test_private_nested_payloads_and_arbitrary_errors_are_never_published](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:74)
- [test_invalid_cli_is_rejected_without_starting_a_client](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:84)
- [test_only_held_owned_popen_can_be_terminated](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:94)
- [test_task_drain_is_bounded_even_when_cancel_is_suppressed](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:106)
- [test_stop_interrupts_provider_wait_without_waiting_for_model_timeout](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:116)
- [test_actual_objectiverunner_cancel_keeps_session_and_releases_foreground](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:124)
- [test_real_codexbrain_cap_raises_before_second_adapter_call](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:150)
- [test_probe_does_not_convert_unrelated_errors_into_budget_evidence](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_lifecycle.py:186)

### spikes/social-faults/test_server_guard.py — 25 functions

- [test_explicit_m8_profile_is_version_and_path_bound_without_changing_m7_defaults](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:48)
- [test_profile_cannot_escape_staging_or_change_types](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:69)
- [test_sandbox_and_social_are_exactly_bound_without_reading_gameplay](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:77)
- [test_lobby_scope_mode_port_status_version_and_freshness_fail_closed](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:85)
- [test_control_plane_requires_exact_exe_pidfile_start_uid_and_listener](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:98)
- [test_server_requires_exact_exe_actor_uid_parent_and_command_flags](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:108)
- [test_udp_ready_and_network_namespace_must_match_same_instance](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:121)
- [test_previous_identity_rejects_pid_reuse_parent_restart_and_aged_inspect](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:127)
- [test_observer_readiness_must_be_live_exact_scope_and_recent](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:137)
- [test_structured_allocator_record_not_arbitrary_prose_and_no_ambiguity](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:147)
- [test_ready_log_requires_actual_exact_full_line](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:154)
- [test_socket_requires_pid_evidence_for_every_bound_socket](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:160)
- [test_proc_stat_handles_spaces_and_parentheses](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:167)
- [test_identity_file_exclusive_private_and_symlink_safe](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:174)
- [test_large_file_is_rejected_or_bounded_to_complete_tail_lines](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:186)
- [test_root_owned_redirect_log_exception_does_not_expand_actor_only_files](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:192)
- [test_redirect_log_trusted_owner_still_rejects_group_or_other_write](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:203)
- [test_projection_never_copies_private_identity_prose_or_exception](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:211)
- [test_inspect_performs_no_pidfd_open_or_signal](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:229)
- [test_terminate_double_revalidates_then_signals_only_held_pidfd](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:235)
- [test_failed_second_validation_never_signals](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:246)
- [test_explicit_abrupt_signal_uses_same_double_validation_and_pidfd](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:258)
- [test_unapproved_signal_never_opens_process_or_sends](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:269)
- [test_expired_or_revoked_observer_never_signals](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:277)
- [test_cli_validation_never_reads_host_or_signals_on_bad_scope](/Users/fpetrov/PycharmProjects/Actor/spikes/social-faults/test_server_guard.py:288)

### spikes/social-privacy/test_probe.py — 21 functions

- [test_actual_surface_coverage_and_zero_violations_are_required](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:47)
- [test_bool_missing_nonfinite_and_duplicate_checks_are_not_evidence](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:60)
- [test_isolation_requires_well_typed_distinct_launch_evidence](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:71)
- [test_actual_kill_must_be_inside_the_same_continuous_far_interval](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:84)
- [test_disjoint_windows_are_not_joined_and_timestamps_never_leak](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:97)
- [test_active_rejection_requires_actual_receipt_and_temporal_far_witness](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:128)
- [test_active_probe_cannot_hide_an_actual_kill_or_export_private_metadata](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:140)
- [test_far_coverage_requires_actual_duration_and_both_surfaces](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:150)
- [test_public_projection_never_copies_private_unknown_fields](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:157)
- [test_postgame_same_scope_and_isolation_are_not_optional](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:165)
- [test_same_frame_pose_is_order_independent_and_no_secret_is_reported](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:218)
- [test_raw_wrong_entity_and_own_recipient_are_counted_before_runtime_filter](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:227)
- [test_legacy_manifest_surface_is_rejected_even_without_live_payload](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:232)
- [test_far_hidden_actor_body_and_attack_are_never_silently_accepted](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:237)
- [test_legitimate_old_witness_event_is_not_misclassified_as_new_far_delivery](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:246)
- [test_wire_interval_gap_never_stitches_two_separate_windows](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:252)
- [test_conflicting_public_match_start_fails_audit](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:263)
- [test_canonical_type_and_early_postgame_are_strict](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:268)
- [test_m8_new_own_bundles_and_presentation_decode_without_becoming_unknown_social_types](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:277)
- [test_m8_raw_foreign_work_roster_and_presentation_are_rejected_before_session_filter](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:291)
- [test_m8_presentation_cannot_disclose_attacker_to_own_victim_or_hidden_far_attack](/Users/fpetrov/PycharmProjects/Actor/spikes/social-privacy/test_probe.py:300)

### spikes/social-protocol/test_fixture.py — 11 functions

- [test_readiness_delay_and_task_sequence_are_role_agnostic](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:58)
- [test_task_stop_wait_then_console_without_starting_work_or_voting](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:69)
- [test_new_phase_provider_uses_retained_public_meeting_to_avoid_restart](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:82)
- [test_real_selection_retains_meeting_only_in_optional_fixture](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:90)
- [test_recreated_phase_brain_does_not_repeat_task_or_second_meeting](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:101)
- [test_actual_initial_selection_keeps_task_park_and_console_sequence](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:108)
- [test_actual_approach_failure_remains_failure](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:121)
- [test_other_phase_capability_selection_is_unchanged](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:129)
- [test_launcher_resolves_installed_console_entrypoint_not_missing_module_main](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:139)
- [test_fixture_input_and_policy_cannot_expand_authority](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:149)
- [test_supervision_requires_four_same_scope_completions_and_no_processes](/Users/fpetrov/PycharmProjects/Actor/spikes/social-protocol/test_fixture.py:165)

### spikes/social-resume/test_run_probe.py — 14 functions

- [test_complete_live_proof_passes_without_exporting_private_input](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:36)
- [test_each_required_claim_fails_closed_when_absent_false_or_truthy_string](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:43)
- [test_postgame_handoff_is_not_live_resume_proof](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:50)
- [test_stationary_old_single_sample_or_nonfinite_duration_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:53)
- [test_udp_fault_requires_actual_loss_in_both_directions_and_bounded_duration](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:57)
- [test_time_order_cannot_be_fabricated_from_only_a_resume_timestamp](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:61)
- [test_cleanup_and_same_owned_process_are_required](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:66)
- [test_numeric_projection_drops_unbounded_or_wrong_types](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:71)
- [test_exact_fresh_m8_staging_room](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:81)
- [test_production_legacy_or_occupied_room_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:82)
- [test_peer_fixture_limits_satisfy_actual_runtime_config_before_admission](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:88)
- [test_opaque_roundtrip_blackhole_restore_and_new_client_port](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:95)
- [test_owned_launcher_exit_does_not_leave_its_child_running](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:118)
- [test_metadata_pid_cannot_authorize_signalling_foreign_session](/Users/fpetrov/PycharmProjects/Actor/spikes/social-resume/test_run_probe.py:146)

### spikes/social-threat/test_audit.py — 19 functions

- [test_plugin_factory_is_actual_codex_with_finite_limits_and_no_fake_adapter_option](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:75)
- [test_before_then_after_and_no_private_projection](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:85)
- [test_wrong_typed_attack_metadata_or_identity_not_fresh](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:98)
- [test_chat_claim_hypothesis_and_visible_activity_not_authoritative](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:115)
- [test_duplicate_event_and_historical_witness_are_distinct](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:128)
- [test_own_crew_alive_and_current_body_required](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:140)
- [test_wrong_body_argument_does_not_match](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:156)
- [test_phase_action_confirmation_is_exact_own_replay_inside_actual_call_window](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:164)
- [test_old_attacker_does_not_match_a_different_fresh_threat](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:187)
- [test_terminal_requires_complete_exact_own_crew](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:200)
- [test_policy_delegates_all_game_behavior_and_only_adds_terminal_audit](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:209)
- [test_scope_change_and_overflow_fail_closed](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:230)
- [test_evaluator_requires_actual_matching_dispatch_and_success](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:242)
- [test_server_confirmed_phase_action_keeps_local_cancelled_status_honest](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:256)
- [test_strict_participation_requires_separate_real_discussion_send_and_vote](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:268)
- [test_discussion_audit_only_retains_channel_boolean_never_message](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:292)
- [test_real_episode_collection_preserves_join_when_ids_are_privately_masked](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:305)
- [test_wrapper_calls_real_codex_brain_unchanged_with_offline_adapter](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:335)
- [test_exception_is_not_replaced_by_decision](/Users/fpetrov/PycharmProjects/Actor/spikes/social-threat/test_audit.py:367)

### spikes/social-validation/test_evaluate.py — 30 functions

- [test_audit_alias_requires_original_official_provider_and_completed_reaction](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:105)
- [test_audit_alias_cannot_fall_back_to_decision_count_or_fake_adapter](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:114)
- [test_audit_candidate_without_dispatch_or_exact_postgame_crew_fails](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:128)
- [test_strict_crew_discussion_vote_requirement_does_not_infer_missing_participation](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:139)
- [test_valid_terminal_independent_match_and_protobuf_uint64_strings](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:147)
- [test_normal_post_despawn_field_clear_keeps_saved_terminal_identity](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:154)
- [test_supervisor_alone_cannot_establish_gameover](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:159)
- [test_missing_any_terminal_proof_or_process_report_rejects](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:164)
- [test_finishobjective_without_authoritative_match_is_insufficient](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:169)
- [test_nonterminal_and_identity_mismatch_cannot_be_overruled_by_supervisor](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:173)
- [test_runtime_accepted_actor_mapping_must_be_unique_and_match](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:183)
- [test_all_four_independent_actor_and_session_identities_required](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:190)
- [test_duplicate_or_missing_summary_roster_actor_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:197)
- [test_different_participant_summaries_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:200)
- [test_forged_isolation_flag_does_not_replace_required_checks](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:204)
- [test_reused_launch_resources_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:207)
- [test_process_cleanup_claim_requires_matching_owned_pid_records](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:213)
- [test_unknown_reason_wrong_winner_and_absent_real_actions_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:220)
- [test_tasks_use_actual_task_replay_and_crew_tasks_counter](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:227)
- [test_sabotage_expired_needs_real_sabotage_not_similar_reason_text](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:242)
- [test_official_unique_model_turns_required_not_decision_count_fallback](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:249)
- [test_both_sides_model_requirement_is_explicit_and_cannot_be_scripted](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:257)
- [test_model_direct_tool_or_provider_mismatch_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:267)
- [test_declared_scripted_provider_cannot_hide_codex_or_unclosed_provider](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:271)
- [test_cleanup_errors_or_pending_tasks_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:279)
- [test_fractional_boolean_negative_nan_counts_fail_closed](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:284)
- [test_replay_order_no_postterminal_actions_and_bounds_required](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:290)
- [test_private_source_payloads_never_copied_to_public_output](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:297)
- [test_malformed_or_missing_inputs_have_bounded_safe_error](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:310)
- [test_missing_participants_and_untrusted_directory_names_fail_closed](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_evaluate.py:316)

### spikes/social-validation/test_performance.py — 18 functions

- [test_exact_four_anonymous_bidirectional_peers_and_payload_rates](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:57)
- [test_missing_peer_missing_direction_and_one_packet_are_not_measurements](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:72)
- [test_extra_peer_is_rejected_instead_of_silently_omitted](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:79)
- [test_unknown_packet_payload_dump_blank_or_wrong_socket_never_gets_skipped](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:84)
- [test_multiple_server_addresses_fail_closed](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:91)
- [test_copy_terminal_blank_does_not_hide_a_packet_but_internal_blank_is_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:96)
- [test_capture_requires_complete_matching_statistics_and_no_kernel_drops](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:101)
- [test_old_candidate_cpu_fields_are_null_and_recipients_are_anonymous](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:107)
- [test_new_cpu_percent_matches_elapsed_and_peak_is_numeric](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:116)
- [test_counts_not_boolean_nonfinite_negative_or_strings](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:123)
- [test_scope_labels_cannot_be_arbitrary_prose](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:128)
- [test_all_four_unique_recipients_and_consistent_visibility_are_required](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:133)
- [test_whitelist_rtt_loop_cpu_heap_and_identity_binding](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:145)
- [test_foreign_match_session_or_duplicate_accepted_id_is_rejected](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:156)
- [test_nonfinite_rtt_is_not_silently_removed_and_optional_reports_are_exactly_four](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:164)
- [test_cleared_client_match_requires_exact_accepted_runtime_session_scope](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:169)
- [test_empty_rtt_remains_unmeasured_instead_of_zero_latency](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:177)
- [test_failure_writes_only_bounded_error_code_no_input_or_addresses](/Users/fpetrov/PycharmProjects/Actor/spikes/social-validation/test_performance.py:184)

Итого в выбранном inventory: 41 Python-файлов, 545 именованных test functions. Это не количество выполненных pytest/unittest cases.
