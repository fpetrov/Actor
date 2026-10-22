# Milestone 8 — release validation

Рабочий отчёт по §78 задания. Снимок составлен только из сохранённых безопасных evidence; новые живые проверки добавляются после их завершения. **PENDING** означает обязательное доказательство, которое ещё не принято, а не PASS. Публичные материалы не содержат credentials, личные сообщения, prompts/reasoning моделей или скрытые факты активного матча. Independent model, mixed Human, deterministic headless, native rendered и offline/unit категории не смешиваются.

## 1. Overall completion status

M8 имеет **6 strictly accepted AI-vs-AI GameOver из 12 зарегистрированных попыток**, включая [пять матчей08–12 с полным M8 binding](models/full-binding-08-12.json), и два полных model-selected social cycle08/12. [Actual stream audit09–12](models/attempts-09-12-stream.json) подтверждает 195 SDK turns, в том числе 28 прерванных, с фактическими tool counters. Отдельный [headless suite](network/headless-m8-suite.json) закрыл шесть выбранных privacy/protocol/loss/late-join case groups с двумя сохранёнными отказами; это не полная fault/native matrix. Human+3 Codex mixed02 дошёл до GameOver, но strict provider proof остаётся неполным. **Полная release acceptance ещё не закрыта**: separate controlled model scenario§61, strict mixed, final native matrix, оставшиеся fault cases и production/security gates открыты. [Матрица](completion-matrix.json) сохраняет границы категорий.

## 2. Baseline и commits

[Baseline](baseline.json): `4a4cbbd46d8dcc8624699e405755a7d457138648`, Unity6000.6.4f1, URP17.6.0, NGO2.13.3. До M8:560 Python tests (491 Runtime +69 scripts),96 subtests; SDK232, ControlPlane23;54 generated artifacts. Текущий source-bound functional snapshot — [`656cdbfa8ab5c11b56ac265cfad02acfcc8ab6c3`](https://github.com/fpetrov/Actor/commit/656cdbfa8ab5c11b56ac265cfad02acfcc8ab6c3). Отдельные evidence и optional acceptance-helper изменения не изменяют собранные Runtime/game products. Clean packaging, GitHub draft integrity, native QA и production activation являются разными gates.

## 3. Design Battle — три концепции

Три независимых автора создали [Luminous Station](../art-direction/concepts/luminous/concept.md), [Quiet Industrial](../art-direction/concepts/industrial/concept.md), [Atmospheric Minimalism](../art-direction/concepts/atmospheric/concept.md). Есть редактируемые векторы, контактные листы и 800×600 композиции. Это реальные concept assets, а не игровые screenshots.

## 4. Результаты design critique

[Таблица независимых оценок](../art-direction/design-battle.md) использует 10 одинаковых критериев. Средние 84/85/85 из 100; Atmospheric получил 2 из 3 первых предпочтений. Субъективные оценки стоимости и производительности не выдаются за измерения.

## 5. Победившая Art Direction

Выбран Atmospheric Minimalism: спокойные полы, тёмные капюшоны, небольшие цветовые акценты, сдержанные стеклянные панели. Замечания конкурентов включены как требования к читаемости шести игроков, крупным landmark и узнаваемым двум светлым глазам.

## 6. Art Style Bible

[Style guide](../art-direction/style-guide.md) фиксирует palette, typography, spacing, состояния, motion, настройки доступности и приватность. Hall — нейтральный, Engineering — sage, Security — lilac, Storage — clay; вся станция использует одну материальную семью.

## 7. Новые/переработанные sprites

[Детерминированный источник](../../art/atmospheric/README.md): 28 editable SVG, 28 PNG и 11 оригинальных PCM cues с hash manifest. Это новые production masters. Исходный atlas и legacy/offline art не заменяются; source-generator не вызывает модель изображения.

## 8. Character assets и skins

Сохранены капюшон и два ivory eyes, шесть направлений и прежнее зеркалирование диагоналей. Шесть mantle colours дополнены шестью stitch marks; сервер выбирает cosmetic по устойчивому admission slot независимо от alignment/duty, resume сохраняет его, corpse фиксирует цвет при смерти. Capsule, motor и prediction не заменены. [Native witness02](native-human/witness-02.json) наблюдал все шесть cosmetics; отдельная матрица контраста всех skins на каждом тёмном полу ещё не принята.

## 9. Kill animation

Реализована 640 ms staged fold presentation с 140 ms cosmetic attacker impulse. В [native victim01](native-human/victim-presentation.json) сняты пять authorized victim end-of-frame poses на 0,042 / 0,183 / 0,341 / 0,624 / 0,804 s: переход к folded silhouette, затем удаление локальной presentation. Victim не получает attacker identity. Ledger/LOS/radius guards реализованы и unit-tested; [actual M8 headless privacy](network/headless-m8-suite.json) проверила NGO/pre-filter RPC/adapter/raw-gRPC surfaces во время реального kill. Это не native animation/audio leakage test; presentation replay после resume и полная покадровая smoothness/duration остаются отдельными gates.

## 10. Corpse/death art

Три отдельные fold-позы и специально нарисованная folded mass заменяют поворот standing sprite. Cosmetic фиксируется при смерти. [Witness02](native-human/witness-02.json) лично видел corpse и сообщил о теле; [victim01](native-human/victim-01.json) получил отдельный [Death view](native-human/death-800.jpg) после собственной server-accepted смерти на 80,518 s. При native Up 1 200 ms own pose точно не изменился. Контраст каждого corpse на каждом полу и replay/privacy после reconnect ещё не проверены полностью.

## 11. Environment redesign

Social мир использует authored floor union, layered walls и terminals без новых art colliders. Hall directory, Security instrument bank, Storage cargo и Engineering reactor дают разные landmarks. [Статический render](world/landmarks-static-world.png), [отчёт](world/landmarks-static-world.json):125 sprites,12 original textures, max dimension1865px. [Semantic audit](world/scene-semantic-audit.json) сохраняет legacy geometry. Native candidate35e выявил настоящий error-magenta StaticSurface shader; [неуспех и remediation](world/shader-remediation.json) сохранены. Исправленный Metal static render имеет0 error-magenta pixels; это не подменяет pending actual native fog/landmark acceptance финального656 client.

## 12. Lighting

Глубина и освещение в основном baked в original artwork; обязательных bloom и множества realtime lights нет. Реальный Metal/URP Renderer2D отрисовал сохранённый мир. Server interest и client fog остаются разными слоями: красивые стены не дают право показать скрытый actor. Полная текущая M8 проверка visual/audio side effects на границе LOS ещё не принята.

## 13. Audio

Есть 11 original short cues, 48 kHz PCM, deterministic source/licensing; Master управляет AudioListener, source применяет EffectsVolume без двойного умножения, есть headless/reduced-motion guards. [Victim presentation](native-human/victim-presentation.json) зарегистрировала ненулевой local DSP RMS/peak на ранних 0,042/0,183 s samples; поздние samples нулевые. Это подтверждает локальный sampled audio output, **не слышимость человеком**, не waveform coverage и не отсутствие других звуков за LOS. All-cue native listening и LOS sound revocation пока открыты; музыка не заявляется.

## 14. UI technology decision

Выбран один retained UI Toolkit overlay stack с общими tokens и view models. Игровые действия идут через существующие authoritative RPC/SDK; UI не определяет исход. Включён Noto Sans с локальным OFL-файлом; nameplate/wayfinding также переведены в retained labels с lifecycle cleanup.

## 15. Glassmorphism implementation

[Фактическая compatibility probe](ui-compatibility/accepted.json) подтверждает shipped blur shader на Metal/URP2D и tinted fallback. Первый неуспех [сохранён](ui-compatibility/attempt-01.json): runtime-created panel не удержал shader в build. После исправления edge energy 0,04783→0,000866; это проверка blur, не usability.

## 16. UI performance/fallback

В отдельной compatibility probe было по 90 frames на condition: mean fallback 10,55 ms, blur 8,33 ms; warmup/vsync не позволяют заключить, что blur быстрее. Fallback остаётся полноценным вариантом и отключается через settings. Есть [native layout samples](ui-integration/layout-validation.json) на четырёх размерах и [реальные gameplay frame windows](human-mixed/native-02.json). Отдельного A/B overhead всего integrated HUD, allocation/GC профиля и phase-transition spike benchmark пока нет.

## 17. Lobby redesign

Retained Lobby содержит обычные create/join/mode/settings и typed server errors. [Native layout](ui-integration/layout-validation.json) подтверждает 800×600, 1280×720, 1920×1080 и 2560×1080, тёмные buttons/popup, Escape/focus restore и Cyrillic paste. После mixed02 исправлен rematch capacity retry: только точный `server_capacity_reached`, 8 s cadence, максимум 75 s, без повторения неизвестного POST результата. [Witness02](native-human/witness-02.json) действительно создал новый round в том же process после bounded retry; старый failure сохранён. [Финальный extracted656 Lobby](release-integrity.json) и [тот же Lobby из скачанного GitHub draft](https-draft-download.json) отдельно проверены в native 800×600 windowed client. Это не room/gameplay acceptance. [Browser proof](web/browser.json) относится к веб-лобби, не Unity.

## 18. HUD redesign

HUD использует permitted own-state/public phase: life/alignment/duty, readiness, task state, actions/cooldowns и сообщения. Четыре пропущенных M8 SDK bindings исправлены;9 production-binding tests проверяют полный catalog, self hydration, TaskStep generation и private presentation. [Full-binding08–12](models/full-binding-08-12.json) подтверждает все4 capabilities в каждом из20 accepted Runtime sessions; за эти5 матчей сервер зарегистрировал26 Crew tasks. Mixed02 сохранил прежний clipped task-control defect; после fix [native witness02](native-human/witness-02.json) реально завершил calibration, sensor comparison и maintenance hold. Полная матрица Agent read-only HUD и всех размеров пока не закрыта.

## 19. Chat redesign

Composer/inbox различают player claim и pending/accepted/rejected transport receipt; server acceptance не означает прочтение Brain. В [native witness02](native-human/witness-02.json) UTF-8 512 bytes достиг ClientReceived, 514 bytes блокировались до send. Native chronology defect исправлен: общая временная лента, поздний ACK не переносит старое сообщение, own echo не дублируется; 7 production presenter tests проверяют порядок, TTL/redaction, focus и отмену autoscroll. Raw chat не публикуется. Финальная native проверка именно последнего chronology fix и IME composition ещё открыта.

## 20. Discussion screen

Model08/09/12 имеют реальные Discussion с тремя Codex senders в каждом: selected+succeeded typed Discuss требует соответствующего server receipt и corroborated recipient counters. Только08/12 также имеют model body-report gate полного цикла. Mixed02 прошёл две встречи с native chat/meeting controls и actual rendered Discussion1280×720. Server acceptance не доказывает прочтение сообщения другой Brain. Шесть участников, длинные имена и полный viewport/focus/scroll sweep после последних layout исправлений пока не приняты.

## 21. Voting screen

Выбор и подтверждение разделены, финальный ballot решает сервер. Model08/09/12 имеют по3 accepted Codex ballots и authoritative ejection;09 остаётся без body-report gate. Mixed02 имеет6 ballots в двух meetings, включая один Human ballot, подтверждённый actor-bound replay. [Voting800×600](human-mixed/voting-800.png) и реальные frame windows опубликованы. Полная native keyboard/Skip/tie/read-only Agent матрица во всех обязательных размерах ещё не закрыта.

## 22. GameOver screen

Terminal view использует authoritative server summary; чужой alignment раскрывается только после GameOver. Strict model01/08–12 имеют terminal agreement; mixed02 получил настоящий parity GameOver. [Native witness02](native-human/gameover-800.jpg) дошёл до обычного900s match_timeout во время Discussion, без winner. Затем actual New Expedition через bounded capacity retry создал [новый round](native-human/rematch-800.jpg) в том же client process: новые actor/match, WaitingForPlayers, own assignment и summary cleared. Это fresh rematch, а не resurrection старого actor.

## 23. UI screenshots

Есть actual standalone [Lobby/Settings/Unicode](ui-integration/layout-validation.json), [final656 Lobby](ui-integration/final-656-lobby-800x600.png), [скачанный draft Lobby](ui-integration/final-656-github-lobby-800x600.png), blur/fallback, [Voting](human-mixed/voting-800.png), [mixed GameOver](human-mixed/gameover-800.png), [witness GameOver](native-human/gameover-800.jpg), [rematch](native-human/rematch-800.jpg), [Death](native-human/death-800.jpg), [victim GameOver](native-human/victim-gameover-800.jpg) и [five native fold frames](native-human/victim-presentation.json). CUA JPEG имеют правильное .jpg; F12 frames остаются настоящими PNG. Прежний [Tasks clipping](human-mixed/tasks-800-defect.png) и [shader35e failure](world/shader-failure-native-35e.jpg) сохранены. Полной screen×800/1280/1920 matrix ещё нет; browser/static render/concepts не подменяют gameplay screenshots.

## 24. Human usability results

[Mixed02](human-mixed/native-02.json) подтвердил native Ready/движение/chat/meeting/accepted ballot. [Witness02](native-human/witness-02.json) прошёл четыре комнаты, завершил все три mechanics, сообщил о теле, проверил шесть skins, UTF-8 512 ClientReceived / 514 blocked и fresh rematch; supervisor 2/5 terminal proofs остаётся incomplete. [Victim01](native-human/victim-01.json) отдельно подтвердил собственную смерть, Death view, блокировку движения реальной клавишей, parity GameOver180,584 s, 3/3 deterministic fixture terminal proofs и cleanup0/native exit0. Это не model/mixed acceptance. IME, audibility и полная resolution matrix пока не закрыты; fullscreen не использовался.

## 25. Human + Agent multiplayer results

[Mixed attempt01](human-mixed/attempt-01.json) остался waiting_timeout/interrupted без Human readiness/gameplay proof. [Mixed02](human-mixed/attempt-02.json) действительно завершился: 659,645 s, Impostor/parity, 2 kills, 1 body report, 2 meetings, 6 ballots, 6 Crew tasks, 2 sabotages; Human имел один accepted ballot. Но strict mixed acceptance **false**: во всех трёх official Codex reports interrupted-turn tool counts неизвестны. 110 reported turns и реальный GameOver не разрешают подставить tools=0. Это настоящий mixed gameplay с незакрытым обязательным provider proof, не принятый strict mixed PASS.

## 26. Match balance changes

Реализованы три одинаковых для Human/Agent server mechanics: timed maintenance hold, трёхшаговая circuit calibration, разнесённые sensor samples. Default threshold8 и hold12s bounded/configurable; authoritative own work/step revision и отдельные receipts исключают локальное «самозавершение». В [full-binding08–12](models/full-binding-08-12.json) принято5 матчей:26 Crew task completions,4 завершения через parity/ejection и1 через tasks. [Условия09–12](models/attempts-09-12-conditions.json) неизменны внутри continuation, но concurrent local Unity builds и различия bindings/instrumentation между историческими cohorts не позволяют выдавать это за balance или win-rate benchmark. 01–07 новые mechanics не доказывают.

## 27. Codex Crew behaviour

Реальные Codex Crew в08/09/12 участвовали в Discussion/accepted ballots и выиграли через ejection;08/12 также сообщили о теле. Model11 завершил8 actual tasks и выиграл через threshold без kill/meeting/vote: это валидный исход, не полный social cycle. За full-binding08–12 зарегистрировано26 server-authoritative Crew tasks. Team win не выдаётся за survival: личная жизнь и actor-bound действия проверяются отдельно. [Реестр01–12](models/attempts-01-12.json) сохраняет failed attempts и selected/succeeded/failed/cancelled/context/model-wait агрегаты; это не доказательство оптимальной стратегии.

## 28. Codex Impostor behaviour

В model01 Codex Impostor самостоятельно выполнил2 accepted kill и1 sabotage и выиграл по parity; в10 — ещё2 kill/1 sabotage и parity. В08/09/12 Impostor был изгнан после actual kill; model11 закончился task victory Crew. [Postgame review08](models/attempt-08-disguise-review.json) фиксирует две выбранные fake-task attempts, одну succeeded. Обе стороны действовали через одинаковые server rules и разрешённый context. Разные budgets/bindings/instrumentation и небольшой denominator не позволяют трактовать результаты как сравнительный win rate.

## 29. Реальные successful kills

Шесть [strict accepted матчей01/08–12](models/attempts-01-12.json) содержат7 actual kills:2/1/1/2/0/1 соответственно. Actor-bound selected actions сопоставлены с ordered postgame replay и Runtime/client terminal identity. В mixed02 ещё2 authoritative kills, но это отдельная категория с неизвестными provider tool counts. Headless kills доказывают gameplay, а не native kill animation/audio timing.

## 30. Body discovery/reporting

Model08 и12 закрывают actual Codex discovery/report gate: выбранный typed ReportBody и server-accepted body report создали meeting, Discussion/Voting/ejection. Model09 имеет meeting/vote, но0 body reports и не считается полным social cycle. В mixed02 тоже был один report, не Human. [Native witness02](native-human/witness-02.json) подтвердил Human body report в own authoritative replay на864,076s после трёх completed mechanics. Видимое тело, намерение сообщить и server acceptance — разные события; native gate подтверждён replay, не одной картинкой.

## 31. Discussion examples (без private raw content)

[Реестр01–12](models/attempts-01-12.json) подтверждает по3 Codex Discussion senders в08/09/12 через selected+succeeded typed Discuss, server receipts и recipient message counters. Общие receipt counters сами по себе не доказывают конкретный канал или смысл; канал здесь подтверждает installed typed Discuss contract. Raw chat, prompts и reasoning не публикуются. Sanitized семантические цитаты, убедительность лжи и изменение чужих beliefs из счётчиков не выводятся.

## 32. Voting

Model08/09/12 имеют по 3 accepted Codex ballots, реальные meeting/Voting и server ejection. Mixed02 отдельно имеет 6 ballots в двух meetings и один actor-bound Human ballot; его strict provider gate false. [Свежий protocol02](network/headless-m8-suite.json) на source656 прошёл все 8 owned-RPC negative/replay checks, включая final-ballot immutability и cancel-before-start-ACK, вместе с 4/4 authoritative Runtime completions и cleanup0. Protocol01 также имел 8/8 RPC, но только 3/4 completions и сохранён как whole FAIL. Исправление затронуло лишь optional fixture context, не Runtime/game authority.

## 33. Impostor deception evaluation

[Ручная postgame оценка08](models/attempt-08-disguise-review.json): grade `observed`, только attempted behavioral disguise. Модель сама выбрала 2 fake-task calls; 1 succeeded сопоставлен по selected/result position и installed typed completion contract, private output оставлен redacted. Отдельного fake-work server replay record нет. Это не доказательство убедительной лжи, смены чужого belief, false alibi/accusation или внутренней мотивации; нельзя назвать поражение/победу оценкой deception quality.

## 34. Results of complete live matches

[Шесть strict accepted AI-vs-AI исходов](models/attempts-01-12.json):

| Attempt | Время,s | Authoritative outcome | Kills | Tasks | Reports | Meetings | Ballots | Full cycle |
|---|---:|---|---:|---:|---:|---:|---:|---|
|01|141,554|Impostor/parity|2|0|0|0|0|нет|
|08|286,153|Crew/impostors_eliminated|1|6|1|1|3|да|
|09|288,414|Crew/impostors_eliminated|1|2|0|1|3|нет|
|10|137,881|Impostor/parity|2|4|0|0|0|нет|
|11|169,599|Crew/tasks_completed|0|8|0|0|0|нет|
|12|274,770|Crew/impostors_eliminated|1|6|1|1|3|да|

[08–12](models/full-binding-08-12.json) —5 independent starts с полным M8 catalog;01 остаётся историческим condition. Mixed02 и deterministic native fixtures не увеличивают AI-vs-AI denominator. **Отдельный controlled model-selected scenario§61 пока не принят**: обычные full cycles08/12 не подменяют специально обозначенную separate validation, а scripted Human fixtures не доказывают модельный выбор.

## 35. Number of attempts / failures / accepted runs

[Реестр01–12](models/attempts-01-12.json):12 зарегистрированных попыток,6 accepted GameOver,6 failed (2 setup,4 Runtime), incomplete0;6 strict independent starts и2 full social cycles. [Full-binding08–12](models/full-binding-08-12.json) отдельно даёт5 starts с20 accepted full-capability Runtime sessions. Mixed01–02, resume3 и Sandbox4 имеют собственные denominators. Старые01–07/01–08 JSON сохраняют прежние snapshot values. [Continuation conditions](models/attempts-09-12-conditions.json): Brain limit1,5m/warning800k, participation3m,900s match/1100s bound; actual failures не исключаются из общего реестра.

## 36. Codex usage

[Весь реестр01–12](models/attempts-01-12.json) содержит known detailed usage291 turns: input7 919 538 (cached5 482 368), output62 314, total7 981 852 tokens. Ещё16 reports не имеют полных turn/tool/usage сведений,8 participants не имеют Runtime report; известный tools0 не распространяется на неизвестные reports. Participation measured_tokens=15 186 209 — другой счётчик. [Actual09–12](models/attempts-09-12-conditions.json):195 turns, input5 203 251 (cached3 608 960), output38 872, total5 242 123. Mixed02 отдельно:110 reported turns /5 140 713 total, interrupted tool counts null. Cost неизвестна; dollar estimate не придумывается.

## 37. Runtime/Skill changes

Runtime добавляет typed discussion, bounded pursuit, context-sensitive Skill catalog, разрешённую working knowledge и generic lifecycle guards; Core не знает Arena roles/tasks/voting. Terminal ledger раскрывает только зарегистрированные Skill IDs без args/outputs. Official single-stream collector сначала проверен [synthetic SDK parity](tests/sdk-stream-parity.json), затем [actual09–12](models/attempts-09-12-stream.json):195 unique SDK turns,28 interrupted с фактическим lifecycle coverage, tool counters0 для всех195. Это новое доказательство не восстанавливает утраченные counters mixed02 или старых failures. Collector/provider lifecycle и game authority проверяются раздельно.

## 38. Navigation improvements

Social navigation использует публичный authored graph, fresh own pose, bounded reroutes и остановку по distinct samples. Pursuit учитывает форму capsules, выбирает current visible/датированное last-seen и не вызывает kill автоматически. В accepted08–12 movement/approach входят в реальные task/report/kill sequences; это не отдельный stress test всех crowded routes. Sandbox [movement retry](sandbox/movement-attempt-02.json) прошёл13 actual synthetic prediction checks, включая wall slide/reversal/reconciliation; native input latency из него не выводится.

## 39. Memory/belief/provenance changes

Working knowledge различает personal observations, player claims и hypotheses. Citation snapshot фиксирует content/time/provenance на момент вывода; latest slot не переписывает старое основание. Protected dependency group устанавливается/evicts атомарно, aliases не становятся независимыми источниками. Typed owner payloads имеют match/recipient/expiry guards; private result не попадает в публичную память по умолчанию. Source/wheel tests подтверждают эти contracts, но не истинность чужого claim или наличие человеческого reasoning.

## 40. Reconnect/rematch

[Credential transport resume](network/reconnect.json): 3 supervisor attempts = 2 setup failures + 1 accepted recovery. Тот же process пережил 30,094 s bidirectional UDP blackhole, сохранил actor/match/private assignment/alive, поднял generation, отверг connected duplicate и old-generation intent; 121 stationary sample / 2,004 s, cleanup0. [Отдельный fresh-ticket late join](network/headless-m8-suite.json) на source656 наблюдался во время настоящей Discussion: 32 samples / 2,325 s, unassigned/passive, commands0, displacement0; прежний actor необратимо Disconnected. Это новый spectator, не credential resume или resurrection. Post-death resume/ghost-vulnerability kill/native reconnect не заявлены. [Native witness02](native-human/witness-02.json) создал новый round в том же process с новыми actor/match и cleared assignment/summary.

## 41. Client FPS

[Native02 v2 frame windows](human-mixed/native-02.json) измерены в обычном Metal standalone после 2 s warmup: Exploration 800×600 — 9 565 focused frames / 79,87 s, 119,76 fps, p95 9,10 ms; Discussion 1280×720 — 4 863 / 40,60 s, 119,78 fps; Voting 800×600 — 2 179 / 18,17 s, 119,89 fps; GameOver800 — 1 682 / 14,02 s, 120,00 fps. Это Unity rendered-loop intervals, не hardware presentation/physical input latency. Discussion800 имеет 0 samples и не считается замером; все phases×resolutions и transition/kill/GC spikes ещё не закрыты.

## 42. Server performance

[Postgame instrumentation](performance/human-mixed-02-postgame.json): 704,914 s от Social initialization до GameOver, включая Waiting; process CPU 8,52% одного logical core, process-lifetime peak147,33 MiB, FixedUpdate50,00/s, Update59,77/s. Visibility p95/max0,2232/11,5315 ms, refresh3,6041/38,2142 ms; p95 только latest1024, maxima all recorded. Это не NGO network tick. Отдельный [15,123 s Exploration sample](performance/mixed-server-03.json) дал CPU9,65%, RSS145,88→146,48 MiB,27threads,UDPdrop delta0; одно короткое окно не доказывает sustained maximum.

## 43. Network performance

Полный postgame counter насчитал 7 452 585 bytes / 34 139 сообщений **только instrumented Social protobuf**; anonymized recipient aggregates опубликованы без IDs. Они исключают transforms/spawns/Standard communication/RPC/NGO/UDP headers и не являются total UDP throughput. 15 s collector мерил socket queues/drops, не packets/throughput/RTT. Resume packet drop counters относятся к injected blackhole. Актуальные M8 full-bandwidth/bridge RTT и сопоставимый M7 baseline benchmark пока отсутствуют; display FPS не выводится из headless rate.

## 44. Automated tests

[Source Runtime545/545 и fresh installed wheel545/545](runtime-package.json) PASS (installed80,71s, outside checkout, без optional SDK/model auth);38 Skills /14 Brains /35 Rules /1 game profile. [Final local .NET](tests/final-dotnet.json):SDK288/288, ControlPlane29/29, включая13 StaticSurfaceRules regressions. [Offline snapshot](tests/final-offline.json):scripts124+96subtests, отдельная пересекающаяся helper/art group153+282, web9, schema54. Эти scopes не складываются в invented total. Source656 также имеет два green GitHub workflows (§50); это не native/game/model acceptance и не изменение545 Runtime proof.

## 45. Native rendered tests

[Layout proof](ui-integration/layout-validation.json) покрывает native Lobby/Settings на четырёх размерах. [Финальный656 extracted client](release-integrity.json) и [authenticated GitHub draft install](https-draft-download.json) действительно показали Lobby 800×600 на Metal; короткие окна около 1 s не являются gameplay/performance benchmark. [Mixed02](human-mixed/native-02.json) даёт Human Discussion/Voting/GameOver/frame windows; [witness02](native-human/witness-02.json) — три mechanics/report/rematch; [victim01](native-human/victim-01.json) — Death/dead-keyboard/parity, [five authorized fold frames](native-human/victim-presentation.json) и sparse DSP. [Shader35e failure](world/shader-remediation.json) не закрывается новым Lobby: final world/fog остаётся отдельной native проверкой. [Web](web/browser.json) — read-only 390/800/1280, Create/Copy не проверены. All-cue audibility, Agent read-only HUD и полная screen×resolution matrix открыты.

## 46. Fault tests

[Actual headless suite](network/headless-m8-suite.json): **8 завершённых case attempts, 6 accepted, 2 failed**, 7 с выполненной процедурой. Source35e: privacy01 strict final PASS, runtime-loss02 PASS, client-loss01 PASS; protocol01 whole FAIL (8 RPC PASS, 3/4 Runtime terminal), runtime-loss01 preflight FAIL (0 clients/Runtime, local bridge address in use). Retry02 явно использовал тот же ещё не заселённый room. Privacy helper первоначально exit1 ожидал server postgame evidence; после получения этого evidence strict offline projection exit0 без второго gameplay launch. Source656: protocol02 whole PASS, Discussion late join01 PASS и guarded Sandbox server SIGKILL01 PASS. [Provenance](network/headless-m8-provenance.json) разделяет exact component stamps; wire/Runtime/authority sources между35e/656 неизменны, shader/native acceptance не переносится. Runtime loss дал наблюдаемый stopped/keyboard-disabled живой client; client-loss→GameOver занял 60,491 s. Server loss дал actual original-process exit, 10 guard + 10 lifecycle checks, stale→terminal 29,521 s, commands0→0: это waiting actor, не moving-loss latency benchmark. На handoff 65 recorded owned PIDs / 7 groups проверены, remaining0, rooms0; optional protocol plugin удалён. [Credential resume](network/reconnect.json) имеет свой denominator. Deployment21 — local transaction tests. Late messages, post-GameOver actions, death during Skill, meeting during actual Codex turn и body/presentation replay после resume этим suite не заявлены.

## 47. Legacy regression

[Sandbox series](sandbox/summary.json) теперь содержит 4 actual case attempts: три accepted checks и один retained setup failure. Movement01 не исполнился из-за старого Development-only probe; новая non-Development opt-in попытка прошла13/13 prediction checks. Cooperation: same Completed calibration + обе Objective succeeded; Investigation: same Resolved/Restored/validated + обе Objective succeeded, normal natural clock. Все четыре distinct ordinary rooms, без моделей/CP mutation, finalcleanup0/inventory0. [Scene semantic audit](world/scene-semantic-audit.json):417documents/132transforms/78colliders, geometrychanged0; originalMainHall и SocialLayout unchanged.

## 48. Versions

M8 набор: Game 0.9.0, admission 4, Arena extension 0.6.0, Runtime 0.7.0; Core wire 1.0. Manifests actor.standard 0.1.0 и actor.standard.communication 0.1.0 unchanged — это не admission/Core version. [Wheel resources/hashes](runtime-package.json), [20 full-binding sessions08–12](models/full-binding-08-12.json) и [clean656 build stamps всех трёх продуктов](release-integrity.json) согласованы. Archives имеют status limited-test-rc, source_dirty=false и не-Development Unity options. Source stamps/checksums/HTTPS draft download не означают production activation, notarization или публичный защищённый transport.

## 49. GitHub commits

Source-bound functional commit — [`656cdbfa8ab5c11b56ac265cfad02acfcc8ab6c3`](https://github.com/fpetrov/Actor/commit/656cdbfa8ab5c11b56ac265cfad02acfcc8ab6c3). Он заменил35e candidate после реального native shader failure; [remediation](world/shader-remediation.json) сохраняет boundary. Последующие docs/evidence и optional fixture-only изменения оформляются отдельно и не пересобирают Runtime/game products. Приватные raw launch/SQLite/model reports в commit не включаются; final evidence HEAD и опубликованный release — отдельные состояния.

## 50. CI results

Для functional656 оба GitHub Actions workflows green: [ControlPlane39701158511](https://github.com/fpetrov/Actor/actions/runs/39701158511) и [SDK+Runtime39701158421](https://github.com/fpetrov/Actor/actions/runs/39701158421). Auth-free Runtime/SDK/helper/art/schema CI не выполняет модели/native acceptance и не активирует VPS. Pillow13.0.0 закреплён только в CI, не production Runtime. Последующий docs/helper evidence commit и source-bound release integrity отслеживаются отдельно от этих functional CI runs.

## 51. VPS deployment

Живые M8 matches/resume/perf/Sandbox получены на изолированном staging, а не доказывают production activation. Новый deploy transaction имеет drain marker, verified nginx cgroup request barrier для старого CP, final serialized zero-room guard и rollback symlink/env600/unit/web; failed rollback сохраняет fail-closed marker. Эти source/local tests проверены независимо. Production M8 activation, actual rollback target behavior, health/version/checksums и HTTPS/release distribution ещё требуют отдельного gate; публичный HTTP нельзя назвать защищённым production transport.

## 52. Release artifacts

[Clean656 release integrity](release-integrity.json) подтверждает 5 source-bound products: macOS client, Linux server, Linux ControlPlane, actor-play Skill и Runtime wheel. Вместе с release-manifest и SHA256SUMS это 7 distribution files; checksum index имеет 6 entries и не хеширует сам себя. [Authenticated HTTPS download draft v0.9.0-rc.1](https-draft-download.json) повторно проверил bytes всех 7 файлов, extracted manifest/executable/stamp и strict ad-hoc signature; скачанный client достиг native Lobby. Это **draft**, не публично опубликованный release или production activation; ad-hoc signing не Apple notarization. [Runtime0.7 proof](runtime-package.json) остаётся 545/545, SHA256 `2aee36a2a60079ba56d53c954d40635b4b9524bf037f7eaa603765ffe0cdee5b`, 206026 bytes. Установка wheel для656 заново не заявляется: unchanged verified wheel использован в существующем environment. Windows/IL2CPP не заявлены; private contexts/logs/SQLite/credentials в release не входят.

## 53. Known limitations

Закрыты минимум 5 independent full-binding starts08–12, actual collector coverage09–12, выбранные [actual M8 headless cases](network/headless-m8-suite.json), clean656 package integrity, два green source CI и authenticated draft HTTPS retrieval. Открыты separate controlled model-selected scenario§61, strict mixed provider proof, audibility/LOS sound revoke, полная screen×resolution/Agent HUD matrix, actual final656 native fog/landmarks, оставшиеся §65 live fault cases, UDP/RTT/GC/transition benchmark, production/security и anonymous public-release gates. Headless privacy не подменяет audiovisual privacy; server loss на waiting actor не доказывает moving-input stop latency. Mixed02 tools null нельзя восстановить новым collector. Shader35e native failure и все historical failed attempts сохранены. Witness02 fixture incomplete2/5, victim01 complete3/3 — deterministic Human QA, не Codex benchmark. Draft/CI/Lobby не равны полной release readiness.

## 54. Future ACTOR SDK roadmap

После закрытия текущей приёмки: развивать generic provenance/contradictions, bounded cognition/episode lifecycle, независимые provider diagnostics и developer-friendly capability registration. Arena roles/tasks/voting остаются game extension, resume credentials — вне Brain/context. Следующий adapter или более сложный интеллект не заменяют незакрытые release gates этой игры; вторую игру, matchmaking cluster, voice/RL/progression в M8 не добавляем.
