# Milestone 7 — Social Deduction: реализация и проверка

Реализованы отдельный SocialDeduction mode, серверные правила матча, ограниченная сетевой видимостью информация и match-lifetime Runtime. **Четыре настоящих scripted-матча прошли строгое terminal acceptance: голосование, parity, саботаж и задачи.** Приняты сквозной privacy-сценарий, Runtime-loss и новый вход Spectator после client-loss. Завершён настоящий матч четырёх Codex: Crew/tasks_completed за72.021s. Дополнительный controlled Crew case строго подтвердил реакцию на видимое тело, Discussion message и ballot. Production0.8.0 опубликован и проверен. Human visual acceptance выполнен частично: native WASD/Fog/chat/GameOver; native Discussion/Voting/death не проверены. Ниже — 48 пунктов §82 с границами доказательств.

Исходный baseline — [`40f6331`](baseline.json). Опубликованные safe evidence: [NGO compatibility](visibility-compatibility.json), [parity](parity.json), [vote](vote.json). JSON содержит postgame outcomes и агрегаты, без чатов, live Alignment, private prompts, credentials и chain-of-thought.

### 1. Общая архитектура

Pure `SocialMatchAuthority` определяет игровую истину. Unity `SocialMatchSession` подставляет approved identities, реальные physical capsule centers, комнаты, LOS и геометрию терминалов; `NetworkSocialMatch` доставляет разрешённые каждому владельцу typed состояния. SDK/Core переносит сообщения, Arena plugin интерпретирует игру, Brain выбирает разрешённые Skills. Подробнее: [архитектура режима](../social-deduction.md), [match lifecycle](../match-lifecycle.md).

### 2. Отличия от legacy modes

`SocialDeduction` выбирается при создании комнаты и использует отдельную сцену. `Sandbox` сохраняет calibration/investigation/station-signals. Social mode исключает legacy duty roster, station-wide position/peer feeds, cooling/diagnostic/calibration observations и broadcast bypass. Публичной команды переключения режима нет; один lobby — один матч.

### 3. Скрытые Alignment

Сервер назначает одного Impostor среди 4–6 ready-участников через CSPRNG. Duty и Alignment выбираются независимо: Engineer/Sheriff не означают Crew. Воспроизводимый seed допускается только в доверенной серверной fixture-конфигурации; ни Brain, ни admission request не задают стороны.

### 4. Хранение собственной и чужой роли

Own Alignment/Duty/Life/cooldowns хранятся на сервере и передаются только owner-targeted protobuf/RPC. У видимых чужих аватаров нет Everyone-readable роли или скрытого duty. Остальным полный состав раскрывается только после GameOver; имя игрока не является восстанавливаемой identity.

### 5. Server-side visibility

Audience вычисляется по текущим физическим позициям, configured radius и LOS через настоящие `SocialVisibilityOccluder` colliders. Живой observer видит только живые разрешённые цели и доступные тела. Неизвестный viewer, невалидная позиция, исключение policy или переполненный ray buffer закрывают видимость. См. [visibility](../visibility.md).

### 6. Fog of War

Visual fog — presentation mesh вокруг локального персонажа с разрешённым radius, без права определять игровую истину. Он следует predicted local presentation и отключён в headless/server. Тёмный renderer сам по себе не считается защитой: закрытие информации обеспечивают server interest management и персональные DTO.

### 7. Исключение скрытых entities из replication

`OnNetworkPreSpawn` устанавливает `SpawnWithObservers=false` и visibility callback до первого spawn. Перед NGO network update сервер применяет Show/Hide; hidden dynamic remote objects действительно despawn. Owner endpoint сохраняется для input/prediction и собственного terminal state. Visible-world — полная замена списков, в том числе пустых; adapter очищает её после 0.5 секунды без получения.

### 8. NGO compatibility spike

**PASS:** NGO 2.13.3, три независимых macOS клиента и Linux dedicated server, по 30 стадий; серверный отчёт также проверен. Initial foreign spawns, duplicate-spawn warnings и policy failures — 0. Проверены owner retention, selective hide/show, 20 повторений, ручной body spawn/move/hide/show/despawn и свежий transform после Show. [Артефакт и границы](visibility-compatibility.json): это actual NGO compatibility, а не integrated Runtime no-leak proof.

### 9. Расширенная карта

Четыре комнаты: Main Hall, Engineering, Security, Storage. Восточный corridor loop соединяет Hall с южным stem; Engineering остаётся нижней ветвью, Security и Storage имеют собственные ответвления. Используются существующий atlas, единый 50 PPU и corridor width 1.15 m. Исходный PNG и legacy сцена не переавторены. [World/geometry](../visibility.md#authored-social-world).

### 10. Navigation changes

Unity экспортирует публичный authored graph и interaction points из той же геометрии в `SocialLayout.json`. Узлы/approach используют actor-root координаты, physical points — capsule center с offset `(0, 0.46)`; capsule `0.28×0.81`. Social navigation подтверждает room transition, четыре distinct stopped samples и cleanup zero. После двух секунд реального застоя возможны максимум два detour по тому же графу с backtrack; альтернативный путь не изобретается.

### 11. Kill

Impostor вызывает typed kill только для текущей видимой цели после собственного cooldown. Действие проходит обычный SDK → owner RPC → серверную authority. Успех меняет life жертвы, создаёт тело и пересчитывает победу; Brain не создаёт смерть и не задаёт её причину. [Elimination](../elimination.md).

### 12. Kill server checks

Проверяются approved ownership, match, точный phase epoch, короткий expiry, correlation/replay, Alive/Impostor, Exploration, cooldown, finite fresh poses, физическая дистанция и LOS. Неизвестная, скрытая, закрытая стеной и далёкая корректно оформленная цель дают единый `target_unavailable`. Payload/identifier bounds и per-owner/global budgets ограничивают probing.

### 13. Death и Body

Death останавливает движение, работу, действия и live communication; own endpoint остаётся для пассивного ожидания результата. Тело содержит victim identity/name и позицию, без killer/Alignment/Duty/global death time. Attack event получают только живые свидетели, видевшие обоих участников в момент атаки. Невидимая смерть не меняет чужой public/own revision.

### 14. Body reporting

Report требует Alive, Exploration, unreported body, действительную видимость и физическую дистанцию (default 1.2 m). Report запускает meeting и скрывает соответствующее тело из доступных bodies. **Actual PASS:** vote fixture содержит одно реальное убийство и один обычный report, без debug-created body.

### 15. Meeting state machine

Сервер ведёт `WaitingForPlayers → RoleAssignment → Exploration → Discussion → Voting → VoteResolution → Exploration/GameOver`. По умолчанию Discussion 90 s, Voting 60 s, Resolution 1 s. Movement, kill и незавершённая работа блокируются; emergency meeting требует настоящего терминала и лимитов. Forfeit в meeting немедленно обновляет разрешённый roll-call. [Правила](../voting.md).

### 16. Discussion и communication permissions

Exploration разрешает `direct`/`proximity` только с серверной видимостью; Discussion/Voting — живую discussion audience. Dead/Ejected/Disconnected/Spectator не пишут живым. Используется существующий Standard Communication, без второго IPC-протокола. ClientReceived receipt подтверждает получение клиентом, а не чтение Brain. Vote fixture зарегистрировал три суммарных доставки discussion messages.

### 17. Voting rules

Один окончательный ballot на живого участника: живой кандидат либо явный Skip; self-vote допустим. До resolution доступен только собственный ballot. Сервер identity определяет по соединению; epoch/replay исключают повторное применение. Disconnect аннулирует ballot forfeiter, а выбывший кандидат не может быть ejected повторно.

### 18. Tie и Skip

Ejection требует единственного eligible leader с числом голосов строго больше Skip. Tie кандидатов, равенство с Skip или отсутствие голосов дают `no_ejection`; пропущенный deadline — abstention, не автоматический Skip. Кворума нет. Эти варианты покрыты pure authority tests; actual vote fixture проверил реальное ejection Impostor.

### 19. Crew victory conditions

Crew побеждают при отсутствии живых Impostors либо достижении общего task threshold. Победа определяется сервером и необратима. `team_won` отличается от `survived`: в actual vote матче погибший Crew принадлежал победившей команде.

### 20. Impostor victory conditions

Impostor побеждает при parity живых сторон или истечении unrepaired life-support emergency. Match timeout — draw. Дедлайны исполняются по времени, при равном timestamp: match timeout, sabotage timeout, phase transition, work completion. Unit tests проверяют гонки; actual parity result приведён ниже.

### 21. Sabotage system

Impostor физически подходит к life-support terminal; сервер проверяет permission, range, room, LOS и cooldown. Авария имеет deadline, один Crew способен непрерывно отремонтировать её. Meeting приостанавливает emergency timer и затем возвращает оставшееся время. Life-support point `(0.8, -6.6)`, authored root approach `(0.3, -7.15)`. Actual sabotage victory подтверждена в [sabotage.json](sabotage.json).

### 22. Crew tasks

У каждого Crew собственные экземпляры четырёх authored task templates в четырёх комнатах. Completion key включает ActorId и template; другой Crew может выполнить собственный экземпляр того же template. Own completion/revision приватны, публичен только aggregate progress. Default threshold 4; допустимый threshold 5 не требует пяти разных терминалов. Работа непрерывна (default 4 s) и проверяется сервером.

### 23. Fake tasks

Impostor может выполнять внешне ту же physical work activity, но без Crew progress. Видимый activity event не содержит alignment или contributes flag. Движение, stale pose, смена фазы, потеря контроля и elimination прерывают работу. Early cancellation поддерживает только текущий собственный work ID либо его original correlation.

### 24. Match-lifetime cognitive architecture

Generic `MatchEpisodeController` получает общие Session/Scheduler/Router/MemoryStore, эксклюзивно координирует когнитивный цикл и bounded Objectives через Arena policy. RuntimeServer владеет транспортным Session lifetime; вызывающий код закрывает общую MemoryStore. У каждого episode свежие Brain и provider thread; между episodes сохраняется bounded private working knowledge. Phase/life/terminal transitions отзывают старый execution до новых команд. `FinishObjective` завершает только episode; participation завершается лишь по authoritative GameOver. [Lifecycle и budgets](../match-lifecycle.md).

### 25. Новые Skills

Arena добавляет ready, perform/fake task, navigate-social-room, approach-visible-actor, kill-visible-actor, report-body, call-meeting, cast-vote, sabotage, repair-emergency, evade-threat и wait-for-match-event. Generic `actor.remember-hypothesis` хранит ограниченное предположение, а существующие send/wait-message обеспечивают общение. Catalog зависит от fresh own state/phase/life; сервер повторяет проверки. Installed wheel обнаружил всего 34 Skills, 14 Brains и 32 EventRules, включая legacy providers.

### 26. Как Codex узнаёт свою Alignment

Только из typed `SocialOwnStateObservation` своего authenticated actor. Общая trusted Objective одинакова всем; ни имя агента, ни launcher, ни модельный prompt не назначают сторону. Official Codex provider выбирает обычные Skills по доступному контексту. **Actual обе стороны подтверждены:** codex-01 содержит три Crew и одного Impostor, четыре настоящих official providers и общий authoritative GameOver. Impostor самостоятельно выбирал fake-task/approach/kill/meeting; успешного kill в этом матче не было.

### 27. Защита от скрытых данных

NGO interest management дополняется owner-targeted envelopes, recipient/type checks, full-replacement visible-world и life/phase communication gates. Historical last-seen не становится текущей позицией; player claims остаются непроверенными с provenance. Private role/action/context не пишутся в автоматическую SQLite/public diagnostics; postgame summary допускается после terminal boundary. [Memory](../memory.md), [context](../context-builder.md).

### 28. Fog of War security tests

**Actual PASS — [privacy.json](privacy.json), третий прогон:** Dedicated Server → NGO incoming RPC/реальные SpawnedObjects → реальные Adapter captures → authenticated Python wire до WorldState. Четыре независимых участия; два удалённых Crew дали согласованные непрерывные интервалы на всех границах, включающие настоящее убийство с запасом до/после. Forbidden facts/recipients/legacy state/private foreign components — 0; раннее раскрытие postgame — 0; все процессы остановлены. Native compatibility и geometry проверены отдельно. Провалы первых двух прогонов сохранены, а не заменены этим PASS.

### 29. Out-of-sight kill

**Actual PASS:** Impostor один раз отправил обычный owned kill RPC по ранее законно виденной, но уже отсутствующей в текущем world цели. Сервер вернул `target_unavailable`: 1 попытка, 1 authoritative rejection, 0 accepted. Этот активный negative probe отделён от пассивного аудита. Затем обычный Brain выполнил настоящий разрешённый kill около 100 s; два далёких Crew не получили убийство, тело, место или attacker identity. Серверный replay сопоставлен с пересечением трёх измеренных интервалов, не с предположением о комнате. Матч естественно завершился draw/timeout 180 s.

`privacy-01` не принят: отсутствовали временные доказательства и kill предшествовал дальнейшему далёкому интервалу. `privacy-02` выявил физический затор двух агентов у ближайшего начального узла; дальнего интервала не было. Исправлен общий navigation connector: до трёх разных authored anchors текущей комнаты, fresh own pose и ограниченная пауза от собственной identity. Никаких скрытых позиций, teleport или фиктивно пройденных рёбер. Пять новых closed-loop collision tests и настоящий `privacy-03` подтвердили исправление. [Первый](privacy-01-incomplete.json) и [второй](privacy-02-incomplete.json) провалы сохранены.

### 30. Deterministic Crew victory

**Actual PASS — [vote.json](vote.json):** четыре независимых ordinary clients/Runtime, общий `social-fixture-vote`. За 32.441 s: 1 kill, 1 body report, 1 meeting, 3 votes, 1 ejection; победитель Crew, причина `impostors_eliminated`. Все terminal identities/summaries согласованы, собственные процессы убраны. Это scripted acceptance, не social-intelligence benchmark.

### 31. Deterministic Impostor parity victory

**Actual PASS — [parity.json](parity.json):** общий `social-fixture-parity`, четыре независимых участника. За 12.689 s — 2 настоящих kill, без report/meeting/task/sabotage. Сервер завершил матч `Impostor/parity`; все terminal proofs и cleanup прошли. Результат не задавался supervisor.

### 32. Sabotage victory

**Actual PASS — [sabotage.json](sabotage.json):** Impostor достиг настоящего life-support терминала, сервер принял sabotage и завершил аварийный таймер. Итог `Impostor/sabotage_expired`, 55.201 s, 1 sabotage, без kill и meeting. Все четыре независимых участия завершились одним authoritative summary; cleanup подтверждён.

### 33. Task victory

**Actual PASS — [tasks.json](tasks.json):** Crew выполнили четыре непрерывные физические работы через собственные назначения. Сервер подтвердил `Crew/tasks_completed` за 86.361 s; Impostor был пассивен. Все четыре клиента/Runtime подтвердили один terminal result. Own-instance privacy и threshold bounds дополнительно покрыты authority tests.

### 34. Настоящий Codex-vs-Codex match

**Actual PASS — [codex-01.json](codex-01.json):** четыре настоящих CodexBrain с одинаковым общим Objective, независимыми процессами, SDK threads, profiles, credentials и SQLite memory. Сервер без заданного seed назначил три Crew и одного Impostor; матч завершился Crew/tasks_completed за 72.021 s. Crew сами выбирали perform-task и navigation. Impostor выбрал fake-task, два approach-visible-actor, попытку kill-visible-actor и call-meeting. Атака не стала подтверждённым убийством; Crew успели закончить задачи. Сообщений, meeting и ballots в этом матче не было; они проверяются отдельным controlled live сценарием. Первый длинный controlled прогон не принят: один Brain завершился brain_failure до общего terminal; supervisor отменил остальных и убрал процессы. В нём были 1 meeting, 3 ballots и входящие communication events, но итоговый server result после cleanup не засчитывается как автономный матч. [Неуспешный прогон сохранён](codex-discussion-01-incomplete.json). Точная provider cause не была удержана; причина не приписывается модели или игровым правилам без доказательства. Strict evaluator подтвердил обе стороны, terminal identity всех четырёх и cleanup; model direct tools = 0.

**Дополнительный controlled model case PASS — [crew-threat-pass.json](crew-threat-pass.json):** один настоящий Codex в заранее фиксированном public slot1 и три обычных parity fixtures; одинаковый общий Objective всем, без seed/role/target/winner hints. Public rules: match240s, kill45s, Discussion60s, Voting60s, work30s, tasks5. Собственный fresh permitted факт предшествовал реальному provider decision и accepted dispatch report-body. Серверный postgame replay подтвердил собственный body_report; локальный Skill был cancelled из-за немедленной смены фазы, поэтому local succeeded остаётся0, server-confirmed completed1. Модель отдельно выполнила server-accepted Discussion send1 и ballot1. Это не доказательство чтения сообщения другим Brain, правильности обвинения или внутренней мотивации. Все четыре участия подтвердили один terminal; модель оказалась Crew только по postgame и погибла на182.055s. Итог Impostor/parity: реакция не объявляется выигрышной стратегией.

[Честный denominator](crew-threat.json): четыре фактических model attempts — один strict PASS, два отказа до terminal (BrokenPipeError и session_disconnected), один завершённый матч без нужного факта: Crew погиб на45.535s. Ещё один setup отказал до создания комнаты/клиентов/модели из-за ConnectionRefused у локального SSH-туннеля. Причинная связь этого обрыва с session_disconnected не установлена. У ранних отказов official terminal turn count неизвестен, а не0. Все owned процессы закрыты; test-only plugin удалён, обычный discovery14Brains/34Skills/32Rules восстановлен. Независимый reviewer повторно запустил strict evaluator и подтвердил PASS.

### 35. Codex turns и usage

Во всех четырёх принятых scripted кейсах Codex turns = **0**, measured Codex usage отсутствует, model tools = **0**. Parity: 10 episodes, 16 decisions, 15 Skill results; Vote: 16 episodes, 25 decisions, 24 Skill results. В настоящем codex-01: **24 turns**, input 527048, cached input 346496, output 3642, total 530690 tokens. По участникам turns 5/5/6/8; средняя latency 9.004/7.212/7.919/7.991 s, максимальная 20.261 s. Это фактические счётчики official SDK0.162.1, read-only ephemeral threads; cached input является частью input. Нули scripted runs не являются оценкой стоимости модели. В принятом controlled Crew case9official turns/0tools; input225680, cached123264, output2117, total227797 tokens; mean provider latency9.120s/max15.689s. Неуспешные попытки и неизвестные terminal counters перечислены отдельно в crew-threat.json и не заменены нулём.

### 36. Выживание каждого агента

Стороны в этой таблице раскрыты **после** GameOver. Длительность в секундах; team victory и личное выживание раздельны.

| Case | Agent | Postgame side | Life | Survival s | Team won | Survived |
|---|---|---|---|---:|---|---|
| Parity | 01 | Crew | Dead | 12.689 | Нет | Нет |
| Parity | 02 | Impostor | Alive | 12.689 | Да | Да |
| Parity | 03 | Crew | Dead | 6.290 | Нет | Нет |
| Parity | 04 | Crew | Alive | 12.689 | Нет | Да |
| Vote | 01 | Crew | Dead | 6.760 | Да | Нет |
| Vote | 02 | Crew | Alive | 32.441 | Да | Да |
| Vote | 03 | Impostor | Ejected | 32.441 | Нет | Нет |
| Vote | 04 | Crew | Alive | 32.441 | Да | Да |
| Codex-01 | 01 | Crew | Alive | 72.021 | Да | Да |
| Codex-01 | 02 | Crew | Alive | 72.021 | Да | Да |
| Codex-01 | 03 | Crew | Alive | 72.021 | Да | Да |
| Codex-01 | 04 | Impostor | Alive | 72.021 | Нет | Да |
| Controlled Crew-05 | 01 | Crew | Dead | 182.055 | Нет | Нет |

### 37. Победитель и причина завершения

Приняты четыре scripted outcome: **Impostor/parity/12.689 s**, **Crew/impostors_eliminated/32.441 s**, **Impostor/sabotage_expired/55.201 s**, **Crew/tasks_completed/86.361 s**. У каждого совпадают dedicated-server summaries, Unity GameOver и Runtime terminal identities. Полные индивидуальные survival/outcome данные всех участников сохранены в соответствующих safe JSON. Настоящий codex-01 завершился **Crew/tasks_completed/72.021s**; Impostor выжил, но проиграл командную цель. Дополнительный controlled Crew-05 — **Impostor/parity/182.055s**; successful threat/discussion/vote coverage не равнозначно победе Crew.

### 38. Human visual checks

**Actual [human-visual.json](human-visual.json) — перечисленный scope PASS:** окно800×600, Ready, own Alignment/Duty HUD, настоящий WASD (0.930m за400ms удержания, затем остановка), туман с физическими границами стен, подавление движения при фокусе чата. Для800UTF-8bytes Send выключен; короткое сообщение получило server-accepted и client-received receipt. Наблюдался настоящий draw/GameOver с четырьмя postgame rows, team result и survival. Native render loop119.32/s — не измерение аппаратного display FPS или keydown latency.

Native Discussion/ballot/death visual **не выполнены**. Во втором запуске выбор QA окна завис на26min и матч истёк; следующий CUA input вернул noWindowsAvailable. Эти попытки не засчитываются. Все свои окна/процессы закрыты; серверные Voting/report/body rules и headless protocol8 проходят отдельно. Fullscreen не использовался. Веб-меню дополнительно [проверено](web-visual.json) на1365px и390px: исправлено переполнение мобильной grid/таблицы, errors0; толькоGET, без создания комнат.

### 39. Fault/reconnect

**Actual Exploration PASS:** [Runtime-loss Crew](fault-runtime-01.json) оставил клиент живым и остановленным с выключенной клавиатурой; сервер зафиксировал необратимый forfeit. [Client-loss и новый вход](fault-reconnect-01.json) создали по свежему ticket неподвижного unassigned Spectator без Alignment, с новой identity. Оба матча законно завершились parity у оставшегося Impostor; процессов не осталось. No-body proof ограничен полным replay и всеми permitted observed views. Дополнительно actual Sandbox [Objective cancel](sandbox-objective-cancel.json) и [официальный Codex turn cap](sandbox-codex-turn-budget.json) прошли на живом обычном клиенте: foreground освобождён, Brain закрыт, pending cleanup0, последующих команд нет. При max_turns1 выполнен настоящий Wait, вторая попытка получила исходный codex_turn_limit; model tools0. Первый observer потери сервера исчерпал собственный240s deadline до пригодного terminal evidence и [сохранён как FAIL](sandbox-server-loss-attempt01.json); корректный exact-instance SIGTERM guard отдельно не превращает его в PASS. Отдельный actual **abrupt server crash PASS**: [exact-instance SIGKILL guard](server-crash-guard.json) и [независимый Sandbox observer](server-crash-observer.json) привязаны к одному собственному scope. После expiry authoritative motion transport завершился за28.820s; контролируемый Wait окончен session_disconnected, клиент оставался жив/остановлен, provider/foreground drained, команды0→0, remaining PIDs0. Это проверка потери сервера, не активного движения под fault. **Actual [Impostor Runtime-loss](fault-impostor-runtime-loss.json) PASS:** заранее фиксированный участник №1 оказался Impostor только по postgame; Crew/impostors_eliminated через0.329s после fault, клиент жив/остановлен, cleanup8/0. Первая из максимум5 запланированных попыток; [denominator сохранён](fault-impostor-attempts.json). **Actual [Discussion reconnect](fault-reconnect-discussion.json) PASS:** выбранный по public Alive участник оказался Crew; fresh sameName ticket дал новую actor/session identity и unassigned неподвижного Spectator.32 свежих samples за2.278s получены внутри той же Discussion/revision, commands0/noAlignment/noMove; cleanup10/0. Последующий Impostor/parity — законный серверный итог через30.320s после fault.

**Actual [protocol.json](protocol.json) PASS:** 8/8 обычных owned-RPC отрицательных проверок: wrong match, expiry, stale phase kill, self-target, exact replay, cross-type correlation conflict, неизменность final ballot и Cancel по start correlation до ACK. Выполнены 10 requests/11 matching results, probe failures0; четыре участия получили один authoritative terminal, cleanup8/0. Test-only plugin удалён из validation environment. Первая попытка controller не стартовала из-за неверного fixture CLI entrypoint; исправлена только fixture, неподтверждённый запуск не засчитан. [Helper](../../spikes/social-faults/README.md):32 offline tests.

### 40. Regression counts

Последние подтверждённые [прогоны](regressions.json); категории не складываются с повторной wheel-проверкой как независимые тесты.

| Проверка | Результат | Scope |
|---|---:|---|
| Runtime source | 491/491, 74.02 s | После обоих physical navigation fixes |
| Fresh installed wheel | 491/491, 74.56 s | Та же окончательная реализация; вне checkout, без optional SDK/auth |
| SDK C# | 232/232 | Включая69 SocialMatchAuthority; recipient sequence и late ACK/rejoin |
| Control Plane | 23/23 | Включая mode/config/admission |
| Scripts | 69 passed + 96 subtests | Один actual pytest run, 0.44 s |
| Protocol artifacts | 54/54 | Freshness/generated agreement |
| Social Unity geometry | 195/195 | Actual capsule sweeps, ±0.08 offsets, terminal room/range/LOS |
| Native NGO compatibility | 30 stages ×3 clients + server | Actual observer/spawn checks |
| New fault helper offline | 32/32 | Auditor/projection/PID rules |
| Lifecycle helper offline | 13 + 22 subtests | Cancellation, exact cap and independent loss observer |
| Protocol fixture offline | 9/9 | Includes installed console entrypoint check |
| Staging instance guard | 23/23 | Exact identity, SIGTERM/SIGKILL, safe projection |
| Sandbox regression helper | 6 + 3 subtests | Strict legacy results and owned cleanup |
| Privacy auditor/projection | 18/18 | Включая temporal и active negative guards |
| Postgame evaluator | 30 methods | Identity/isolation/terminal/model доказательства |
| Performance projection | 18/18 | Header-only parser и безопасные агрегаты |
| Transparent threat audit offline | 19/19 | Actual Context ordering, accepted decisions, postgame-only projection |

Окончательный wheel: `build/milestone7-package-final.json`, SHA256 `c5268f364c9755f153608f49b670a0e94811d3ca1804b5d1612ca468fab0cf41`,179212 bytes. Проверены current canonical map, fixture100s, connector fallback и same-room service route; ранние package snapshots заменены этим доказательством. Geometry: `build/social-world/geometry.json`. После финальной presentation-сборки actual Editor prediction queries прошли **12/12**, включая первый кадр до snapshot, capsule wall/slide, timeout/recovery/order/wrap и recent release under high RTT; [безопасный отчёт](prediction-queries.json). Baseline M6 evidence сохранены; Semantic [legacy-assets.json](legacy-assets.json) подтвердил все417 MainHall YAML objects и прежние capsule/input/prediction параметры; serialization-only diff удалён. **Actual legacy live regression:** [movement](sandbox-movement.json)13/13 PASS (synthetic headless): visual28.014ms/authoritative345.766ms/release112.414ms, idle error0.004838m/maxspeed2.600, errors/warnings0. [Investigation first attempt](sandbox-investigation.json)FAIL выявил общий тесный staging point для Engineer/Sheriff. Семистрочный Arena-only fix ведёт same-room MainHall service через широкую нижнюю полку, используя fresh own pose. Добавлены3 physical regression cases. [Повторный investigation](sandbox-investigation-retry.json)PASS:31.640/34.534s, оба Resolved/Restored/reportValidated. [Final cooperation](sandbox-cooperation-retry.json)PASS:13.185/16.047s, sameCompletedTask и настоящая private DM/ACK; все собственные процессы убраны. Ни Core, ни Unity geometry/motor/proto для этого исправления не менялись. Полные source491 и fresh installed wheel491 выше включают это исправление. Полный .NET прогон после communication security tests: SDK232+ControlPlane23; скрытый A→B трафик не оставляет sequence gaps уC, поздний ACK после timeout/disconnect/rejoin не меняет cached rejection и не доставляет сообщение повторно.

### 41. FPS и сеть

**Actual [performance-privacy-01.json](performance-privacy-01.json):** server physics 49.991 Hz, Update 59.629 Hz; visibility p95 0.277 ms/max 9.511 ms; Social authority refresh p95 2.861 ms/max 35.303 ms. Последнее не выдаётся за время всего Unity physics tick. Четыре клиента: 58.984–59.050 headless loops/s, **не display FPS**. Header-only capture: 56626 UDP packets, 0 kernel drops; C→S 1,060,406 payload bytes, S→C 4,690,299 за 122.450 s packet-active interval. IP/UDP headers исключены; Social protobuf counters считаются отдельно. Loopback gRPC RTT приведён отдельно от VPS RTT. Временной пробел privacy-01 не отменяет измерения производительности; невидимое убийство этим отчётом не доказывается. Аппаратный display FPS не измерен.

Второй независимый [performance-privacy-02.json](performance-privacy-02.json): server physics 49.997 Hz, Update 59.746 Hz, CPU **8.008% одного ядра**, peak RSS **150818816 bytes** (~143.83 MiB). Visibility p95 0.287 ms/max 6.144 ms; authority refresh p95 3.479 ms/max 37.277 ms. Серверное окно 244.617 s включает ожидание участников; UDP окно 182.668 s. Четыре bidirectional peers: 82769 захваченных пакетов, kernel drops 0; C→S 1,600,294 и S→C 7,531,195 UDP payload bytes. Метки peers, protobuf recipients и local participants независимы: socket-to-ActorId соответствие этим capture не установлено.

### 42. Protobuf changes

Arena добавляет [`social.proto`](../../proto/extensions/agents_arena/v1/social.proto): public match/rules, owner state/tasks, personal visible-world, meeting/emergency, bounded action context/results, scoped events и postgame summary/replay. Alignment отсутствует в foreign visible actor. C#/Python/descriptor/manifest генерируются из одной схемы; transport и Core не превращены во второй game API.

### 43. Версии

Game **0.8.0**, Arena extension **0.5.0**, Runtime **0.6.0**. Core protocol **1.0**, admission **3**, Standard motion extension **0.1.0**, Communication **0.1.0**. Fresh wheel проверил packaged descriptors/resources и discovery без optional Codex/Jev SDK. Проверенный production release20261021T193053Z-89c00dd привязан к functional commit89c00dd; хэши ниже.

### 44. VPS deployment

**Actual production PASS — [deployment.json](deployment.json):** release `20261021T193053Z-89c00dd` на5.181.134.236. Перед единственным disruptive stop после загрузки файлов повторно проверено0active rooms. Existing environment изменён только для GameVersion0.7→0.8, private0600 backup сохранён. Health healthy; game0.8.0/admission3; Sandbox capacity8, Social min4/max6, общая capacity5. Backend слушает только loopback5080; `/internal/` закрыт nginx. Remote executable/managed DLL/web hashes совпали с локальными проверенными build outputs; старые release directories сохранены.

**[Production smoke PASS](production-smoke.json):** собственная ordinary Social комната дошла доReady, обычный headless Human-mode клиент прошёл HTTP/UDP admission и получил owner spawn/typed WaitingForPlayers без преждевременного назначения стороны.11.653s,0errors/0warnings, commands0. Клиент закрыт, комната освобождена штатно, финальные health healthy/active rooms0. Это smoke startup/admission, отдельный от полноценных staging матчей. **[Cleanup PASS](cleanup.json):** exact staging CP остановлен через pidfd только после0rooms и свободногоUDP; API5081 и собственный SSH tunnel закрыты, UDP7781 возвращён production. Все model/fixture процессы убраны, optional plugins удалены.

### 45. Git commits

Baseline — [`40f6331`](baseline.json). Functional M7 commit [`89c00dd`](https://github.com/fpetrov/Actor/commit/89c00dd1aa061ee0985b90e386567b945b1ab3f6) опубликован в public main.228 source/test/asset/safe-evidence files; build outputs, браузерные snapshots, raw runtime logs, SQLite и credentials исключены. Отдельный documentation commit содержит итоговые live/release/CI evidence; production binaries привязаны к functional hash89c00dd.

### 46. GitHub CI

**PASS для functional commit89c00dd:** [Control Plane](https://github.com/fpetrov/Actor/actions/runs/39643626220) и [ACTOR SDK/Runtime](https://github.com/fpetrov/Actor/actions/runs/39643626148). Hosted Ubuntu24.04/.NET10/Python3.12 проверили C#, generated freshness, Runtime/scripts, auth-free Social/fault/privacy/threat helpers и deterministic event routing. [Machine-readable evidence](ci.json). CI не запускает модели, Unity-клиенты или VPS deployment; их actual evidence приведены отдельно.

### 47. Ограничения MVP

Один матч на lobby, один Impostor, 4–6 игроков, bounded tasks/meetings/replay; timeout — draw. Нет authenticated reconnect identity, role handoff, rematch, spectator free-camera, vents, voice, inventory, training и статистического вывода о качестве social reasoning. Четыре принятых scripted матча, один четырёхмодельный матч, один controlled Crew case и отдельные fault cases не являются статистическим benchmark. Неполные native visual проверки перечислены в§38; остальные результаты и неуспешные попытки имеют собственные evidence и scope.

### 48. Следующий рекомендуемый milestone

После завершения M7 acceptance — bounded cooperative beliefs и role/identity handoff: явное различение наблюдений, доставленных claims и гипотез, объяснимые citations и безопасное продолжение участия после разрешённого reconnect. Оценивать это стоит воспроизводимыми обе-стороны сценариями и honest denominators, сохраняя единственную серверную игровую истину и существующий typed SDK.
