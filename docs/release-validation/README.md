# Milestone 8 — release validation

Рабочий отчёт по §78 задания. Снимок составлен только из сохранённых безопасных evidence; новые живые проверки добавляются после их завершения. **PENDING** означает обязательное доказательство, которое ещё не принято, а не PASS. Публичные материалы не содержат credentials, личные сообщения, prompts/reasoning моделей или скрытые факты активного матча. Independent model, mixed Human, deterministic headless, native rendered и offline/unit категории не смешиваются.

## 1. Overall completion status

M8 подтверждён реальными standalone и dedicated-server прогонами: два strictly accepted матча четырёх Codex, один полный model-selected social cycle, фактический Human+3 Codex GameOver, три Sandbox regression PASS и transport recovery. Native witness02 подтвердил три Human task mechanics, body report, bounded chat и fresh rematch; victim01 — смерть, неподвижность после неё и actual fold frames. Это **ещё не полная release acceptance**: strict mixed provider gate, пять подтверждённых Codex starts, свежие M8 privacy/protocol/fault suites, полная native matrix и final release/deploy/CI открыты. [Матрица](completion-matrix.json) сохраняет границы.

## 2. Baseline и commits

[Baseline](baseline.json): `4a4cbbd46d8dcc8624699e405755a7d457138648`, Unity 6000.6.4f1, URP 17.6.0, NGO 2.13.3. До M8: 560 Python tests (491 Runtime + 69 scripts), 96 subtests; SDK 232, ControlPlane 23; 54 generated artifacts. Текущий Git HEAD остаётся baseline; функциональные M8 изменения и evidence находятся в working tree. Финальный опубликованный M8 commit и clean release build пока не зафиксированы.

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

Реализована 640 ms staged fold presentation с 140 ms cosmetic attacker impulse. В [native victim01](native-human/victim-presentation.json) реально сняты пять authorized victim end-of-frame poses на 0,042 / 0,183 / 0,341 / 0,624 / 0,804 s: виден переход к folded silhouette, затем удаление локальной presentation. Victim не получает attacker identity. Ledger/LOS/radius guards реализованы и unit-tested; отдельная actual M8 privacy проверка свидетелей и отсутствие replay после resume остаются открыты. Пять sparse frames не являются полной покадровой длительностью или smoothness benchmark.

## 10. Corpse/death art

Три отдельные fold-позы и специально нарисованная folded mass заменяют поворот standing sprite. Cosmetic фиксируется при смерти. [Witness02](native-human/witness-02.json) лично видел corpse и сообщил о теле; [victim01](native-human/victim-01.json) получил отдельный [Death view](native-human/death-800.jpg) после собственной server-accepted смерти на 80,518 s. При native Up 1 200 ms own pose точно не изменился. Контраст каждого corpse на каждом полу и replay/privacy после reconnect ещё не проверены полностью.

## 11. Environment redesign

Social мир использует authored floor union, layered walls и terminals без новых art colliders. Hall directory, Security instrument bank, Storage cargo и Engineering reactor дают разные landmarks. [Статический render](world/landmarks-static-world.png), [отчёт](world/landmarks-static-world.json): 125 sprites, 12 original textures, max dimension 1865 px. [Semantic audit](world/scene-semantic-audit.json) сохраняет legacy geometry. Это статическая проверка без actors/fog/UI; native видимость новых props под fog проверяется отдельно и не выводится из preview.

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

Retained Lobby содержит обычные create/join/mode/settings и typed server errors. [Native layout](ui-integration/layout-validation.json) подтверждает 800×600, 1280×720, 1920×1080 и 2560×1080, тёмные buttons/popup, Escape/focus restore и Cyrillic paste. После mixed02 исправлен rematch capacity retry: только точный `server_capacity_reached`, 8 s cadence, максимум 75 s, без повторения неизвестного POST результата. Actual successful fresh rematch после этой правки ещё не принят. [Browser proof](web/browser.json) отдельно относится к веб-лобби, не Unity.

## 18. HUD redesign

HUD использует permitted own-state/public phase: life/alignment/duty, readiness, task state, actions/cooldowns и сообщения. Четыре пропущенных M8 SDK bindings исправлены; 9 production-binding tests проверяют полный catalog, self hydration, TaskStep generation и private presentation. Model08 реально завершил шесть Crew tasks. Mixed02 сохранил прежний clipped task-control defect; после fix [native witness02](native-human/witness-02.json) реально завершил calibration, sensor comparison и maintenance hold. Полная матрица Agent read-only HUD и всех размеров пока не закрыта.

## 19. Chat redesign

Composer/inbox различают player claim и pending/accepted/rejected transport receipt; server acceptance не означает прочтение Brain. В [native witness02](native-human/witness-02.json) UTF-8 512 bytes достиг ClientReceived, 514 bytes блокировались до send. Native chronology defect исправлен: общая временная лента, поздний ACK не переносит старое сообщение, own echo не дублируется; 7 production presenter tests проверяют порядок, TTL/redaction, focus и отмену autoscroll. Raw chat не публикуется. Финальная native проверка именно последнего chronology fix и IME composition ещё открыта.

## 20. Discussion screen

Model08 прошёл настоящую Discussion: три Codex sender имеют выбранный и succeeded typed Discuss, требующий совпадающего server receipt; это corroborated сообщениями у recipients. Mixed02 также прошёл две встречи, с native chat/meeting controls и actual rendered Discussion 1280×720. Шесть участников, длинные имена и полный viewport/focus/scroll sweep после последних layout исправлений пока не приняты.

## 21. Voting screen

Выбор и подтверждение разделены, финальный ballot решает сервер. Model08 дал три accepted Codex ballots и authoritative ejection; mixed02 — шесть ballots в двух meetings, включая один Human ballot, подтверждённый actor-bound replay. [Voting 800×600](human-mixed/voting-800.png) и реальные frame windows опубликованы. Полная native keyboard/Skip/tie/read-only Agent матрица во всех обязательных размерах ещё не закрыта.

## 22. GameOver screen

Terminal view использует authoritative server summary; чужой alignment раскрывается только после GameOver. Model01/08 имеют strict terminal agreement; mixed02 получил настоящий parity GameOver. [Native witness02](native-human/gameover-800.jpg) дошёл до обычного 900 s match_timeout во время Discussion, без winner. Затем actual New Expedition через bounded capacity retry создал [новый round](native-human/rematch-800.jpg) в том же client process: новые actor/match, WaitingForPlayers, own assignment и summary cleared. Это fresh rematch, а не resurrection старого actor.

## 23. UI screenshots

Есть actual standalone [Lobby/Settings/Unicode](ui-integration/layout-validation.json), blur/fallback, [Voting](human-mixed/voting-800.png), [mixed GameOver](human-mixed/gameover-800.png), [witness GameOver](native-human/gameover-800.jpg), [rematch](native-human/rematch-800.jpg), [Death](native-human/death-800.jpg), [victim GameOver](native-human/victim-gameover-800.jpg) и [five native fold frames](native-human/victim-presentation.json). CUA JPEG имеют правильное .jpg; F12 frames остаются настоящими PNG. Прежний [Tasks clipping](human-mixed/tasks-800-defect.png) сохранён. Полной screen×800/1280/1920 matrix всё ещё нет; browser/static render/concepts не подменяют gameplay screenshots.

## 24. Human usability results

[Mixed02](human-mixed/native-02.json) подтвердил native Ready/движение/chat/meeting/accepted ballot. [Witness02](native-human/witness-02.json) прошёл четыре комнаты, завершил все три mechanics, сообщил о теле, проверил шесть skins, UTF-8 512 ClientReceived / 514 blocked и fresh rematch; supervisor 2/5 terminal proofs остаётся incomplete. [Victim01](native-human/victim-01.json) отдельно подтвердил собственную смерть, Death view, блокировку движения реальной клавишей, parity GameOver180,584 s, 3/3 deterministic fixture terminal proofs и cleanup0/native exit0. Это не model/mixed acceptance. IME, audibility и полная resolution matrix пока не закрыты; fullscreen не использовался.

## 25. Human + Agent multiplayer results

[Mixed attempt01](human-mixed/attempt-01.json) остался waiting_timeout/interrupted без Human readiness/gameplay proof. [Mixed02](human-mixed/attempt-02.json) действительно завершился: 659,645 s, Impostor/parity, 2 kills, 1 body report, 2 meetings, 6 ballots, 6 Crew tasks, 2 sabotages; Human имел один accepted ballot. Но strict mixed acceptance **false**: во всех трёх official Codex reports interrupted-turn tool counts неизвестны. 110 reported turns и реальный GameOver не разрешают подставить tools=0. Это настоящий mixed gameplay с незакрытым обязательным provider proof, не принятый strict mixed PASS.

## 26. Match balance changes

Реализованы три одинаковых для Human/Agent server mechanics: timed maintenance hold, трёхшаговая circuit calibration, разнесённые sensor samples. Default threshold 8 и hold 12 s bounded/configurable; authoritative own work/step revision и отдельные receipts исключают локальное «самозавершение». В model08 после полного binding было шесть actual Crew task completions, матч шёл 286,153 s и завершился ejection, а не task rush. Это один подтверждённый новый condition, не повторяемый balance benchmark; 01–07 новых mechanics не доказывают.

## 27. Codex Crew behaviour

В [model08](codex-attempt-08.json) настоящие Codex Crew завершили шесть server-authoritative tasks; один Crew сообщил о теле, выжившие участвовали в Discussion и ballots, выиграли через ejection Impostor. Один Crew погиб на 99,981 s; team win не выдается за survival. Полные selected/succeeded/failed/cancelled counts и context/model-wait агрегаты есть в [series](models/attempts-01-08.json). Одна такая игра не доказывает устойчивую компетенцию или оптимальную стратегию; неуспешные попытки 02–07 сохранены.

## 28. Codex Impostor behaviour

В model01 Codex Impostor самостоятельно выполнил 2 accepted kill и 1 sabotage и выиграл по parity. В model08 другой реальный Impostor сделал 1 accepted kill, дважды выбрал fake task и был изгнан; один fake task succeeded, второй failed. Обе стороны действовали через одинаковые server rules и разрешённый context. Эти исходы не превращаются в сравнительный win rate: budgets, bindings и Runtime instrumentation между условиями менялись.

## 29. Реальные successful kills

[Strict01](codex-attempt-01.json) подтверждает 2 kills, [strict08](codex-attempt-08.json) — ещё 1; actor-bound selected actions сопоставлены с bounded ordered postgame replay и Runtime/client terminal identity. В mixed02 было ещё 2 authoritative kills, но это отдельная категория с неизвестными provider tool counts. Headless kills доказывают gameplay, а не native kill animation/audio timing.

## 30. Body discovery/reporting

Model08 закрыл actual Codex discovery/report gate: выбранный typed ReportBody и server-accepted body report создали meeting, Discussion/Voting/ejection. В mixed02 тоже был один report, не Human. Позже [native witness02](native-human/witness-02.json) подтвердил настоящий Human body report в own authoritative replay на 864,076 s, после трёх completed tasks. Показать тело, намерение сообщить и server acceptance — разные события; здесь native gate подтверждается replay, не одной картинкой.

## 31. Discussion examples (без private raw content)

Публичный [full-social-cycle record](models/attempts-01-08.json) model08 подтверждает три Codex Discussion senders через selected+succeeded typed Discuss и server receipts; recipients получили 14/12/1/15 сообщений за игру. Эти общие receipt counters сами по себе не доказывают конкретный канал или смысл; канал подтверждает контракт typed Discuss. Raw chat, private prompts и reasoning не публикуются. Sanitized семантические цитаты сообщений не выдумываются из счётчиков.

## 32. Voting

Model08 имеет 3 actual accepted Codex ballots, meeting/Voting и server ejection. Mixed02 отдельно имеет 6 ballots в двух meetings и один actor-bound Human ballot; его строгий provider gate остаётся false. Skip/tie/final-ballot immutability проверяются deterministic authority/protocol tests, но текущий M8 live eight-case negative suite ещё не выполнен и не подменяется M7 evidence.

## 33. Impostor deception evaluation

[Ручная postgame оценка08](models/attempt-08-disguise-review.json): grade `observed`, только attempted behavioral disguise. Модель сама выбрала 2 fake-task calls; 1 succeeded сопоставлен по selected/result position и installed typed completion contract, private output оставлен redacted. Отдельного fake-work server replay record нет. Это не доказательство убедительной лжи, смены чужого belief, false alibi/accusation или внутренней мотивации; нельзя назвать поражение/победу оценкой deception quality.

## 34. Results of complete live matches

Два strict complete Codex GameOver: 01 — 141,554 s, parity, 2 kills/1 sabotage без meeting/vote/task; 08 — 286,153 s, Crew/impostors_eliminated, 1 kill/1 report/1 meeting/3 ballots/6 tasks. Только 08 доказывает full model-selected social cycle. Mixed02 — отдельный actual 659,645 s cycle с Human, provider proof неполон. Deterministic native witness02 завершился 900 s match_timeout/no winner с 2/5 terminal proofs, victim01 — 180,584 s parity с 3/3. Они проверяют Human presentation/input, не добавляют Codex starts; минимум пяти ещё не доказан.

## 35. Number of attempts / failures / accepted runs

[Реестр01–08](models/attempts-01-08.json): 8 зарегистрированных попыток, 2 accepted GameOver, 6 failed — 2 setup и 4 Runtime failures; incomplete 0. Строго подтверждённых unique Codex starts — 2, full social cycle — 1. Mixed01–02, resume3 и Sandbox4 имеют собственные denominators и не увеличивают AI-vs-AI series. [Conditions](models/attempts-01-08-conditions.json) отмечают новые bindings и budgets08: Brain limit 1,5m, warning 800k, participation limit 3m. Это не одинаковые условия для win-rate pooling.

## 36. Codex usage

01: 37 official turns / 951 186 total tokens. 08: 59 turns, 1 773 301 input (1 228 928 cached), 15 242 output, 1 788 543 total, direct tools 0. За весь реестр known detailed usage — 96 turns / 2 739 729 total tokens; ещё 16 reports не имеют полных turn/tool/usage сведений, 8 participants не имеют Runtime report. Participation measured_tokens=9 944 086 — другой счётчик, не восстановленная provider usage. Mixed02 отдельно: 110 reported turns / 5 140 713 total, tool counts null. Cost везде неизвестна; dollar estimate не придумывается.

## 37. Runtime/Skill changes

Runtime добавляет typed discussion, bounded pursuit, context-sensitive Skill catalog, разрешённую working knowledge и generic lifecycle guards. Core не знает Arena roles/tasks/voting. Терминальный ledger раскрывает только зарегистрированные Skill IDs, без args/outputs. После обнаруженного mixed02 telemetry gap official stream collector получил future-facing accounting guards; [SDK parity](tests/sdk-stream-parity.json) сравнивает synthetic typed completed/interrupted notifications без model calls. Это не восстанавливает утраченные tool counts старого mixed02 и не доказывает будущий live run.

## 38. Navigation improvements

Social navigation использует публичный authored graph, fresh own pose, bounded reroutes и остановку по distinct samples. Pursuit учитывает форму capsules, выбирает current visible/датированное last-seen и не вызывает kill автоматически. В model08 реальные navigation/approach были частью task/report/kill sequence; это не отдельный stress test всех crowded routes. Sandbox [movement retry](sandbox/movement-attempt-02.json) прошёл 13 actual synthetic prediction checks, включая wall slide/reversal/reconciliation; native input latency из него не выводится.

## 39. Memory/belief/provenance changes

Working knowledge различает personal observations, player claims и hypotheses. Citation snapshot фиксирует content/time/provenance на момент вывода; latest slot не переписывает старое основание. Protected dependency group устанавливается/evicts атомарно, aliases не становятся независимыми источниками. Typed owner payloads имеют match/recipient/expiry guards; private result не попадает в публичную память по умолчанию. Source/wheel tests подтверждают эти contracts, но не истинность чужого claim или наличие человеческого reasoning.

## 40. Reconnect/rematch

[Transport proof](network/reconnect.json): 3 supervisor attempts = 2 setup failures + 1 accepted recovery. Тот же process пережил 30,094 s bidirectional UDP blackhole, сохранил actor/match/private assignment/alive, поднял generation, отверг connected duplicate и old-generation intent; 121 stationary sample / 2,004 s, cleanup 0. Post-death resume/ghost-vulnerability kill/native reconnect не заявлены. После сохранённого mixed02 capacity failure [native witness02](native-human/witness-02.json) действительно создал новый round в том же process: новые actor/match, Waiting, assignment/summary cleared.

## 41. Client FPS

[Native02 v2 frame windows](human-mixed/native-02.json) измерены в обычном Metal standalone после 2 s warmup: Exploration 800×600 — 9 565 focused frames / 79,87 s, 119,76 fps, p95 9,10 ms; Discussion 1280×720 — 4 863 / 40,60 s, 119,78 fps; Voting 800×600 — 2 179 / 18,17 s, 119,89 fps; GameOver800 — 1 682 / 14,02 s, 120,00 fps. Это Unity rendered-loop intervals, не hardware presentation/physical input latency. Discussion800 имеет 0 samples и не считается замером; все phases×resolutions и transition/kill/GC spikes ещё не закрыты.

## 42. Server performance

[Postgame instrumentation](performance/human-mixed-02-postgame.json): 704,914 s от Social initialization до GameOver, включая Waiting; process CPU 8,52% одного logical core, process-lifetime peak147,33 MiB, FixedUpdate50,00/s, Update59,77/s. Visibility p95/max0,2232/11,5315 ms, refresh3,6041/38,2142 ms; p95 только latest1024, maxima all recorded. Это не NGO network tick. Отдельный [15,123 s Exploration sample](performance/mixed-server-03.json) дал CPU9,65%, RSS145,88→146,48 MiB,27threads,UDPdrop delta0; одно короткое окно не доказывает sustained maximum.

## 43. Network performance

Полный postgame counter насчитал 7 452 585 bytes / 34 139 сообщений **только instrumented Social protobuf**; anonymized recipient aggregates опубликованы без IDs. Они исключают transforms/spawns/Standard communication/RPC/NGO/UDP headers и не являются total UDP throughput. 15 s collector мерил socket queues/drops, не packets/throughput/RTT. Resume packet drop counters относятся к injected blackhole. Актуальные M8 full-bandwidth/bridge RTT и сопоставимый M7 baseline benchmark пока отсутствуют; display FPS не выводится из headless rate.

## 44. Automated tests

Последние локальные source Runtime 545/545 и [fresh installed wheel](runtime-package.json) 545/545 PASS (installed 80,71 s, no optional SDK/model auth); 38 Skills / 14 Brains / 35 Rules / 1 game profile. Новая полная .NET проверка: **SDK 288/288, ControlPlane 29/29**, включая 13 StaticSurfaceRules regressions; schema freshness 54 artifacts и web 9/9 PASS. [Auxiliary311+332](tests/auxiliary-verification.json) и [поздний subset159+242](tests/auxiliary.json) пересекаются, не складываются. Presenter 7, SocialBindings 9, deployment 21, optional native-fixture 19 и world 195 — разные bounded scopes. Final source-commit binding/CI ещё нужен.

## 45. Native rendered tests

[Layout proof](ui-integration/layout-validation.json) покрывает native Lobby/Settings на четырёх размерах. [Mixed02](human-mixed/native-02.json) даёт Human Discussion/Voting/GameOver/frame windows; [witness02](native-human/witness-02.json) — три mechanics/report/rematch; [victim01](native-human/victim-01.json) — Death/dead-keyboard/parity, [five authorized fold frames](native-human/victim-presentation.json) и sparse DSP. [Web](web/browser.json) — actual read-only 390/800/1280; Create/Copy не проверены. [World195](world/geometry.json) — capsule/room/LOS checks. All-cue audibility, Agent read-only HUD и полная screen×resolution matrix остаются открыты.

## 46. Fault tests

Текущий M8 actual fault PASS — [resume](network/reconnect.json), с двумя сохранёнными setup failures. Deployment21 — local transaction/rollback tests, не VPS crash. Version-bound wrappers подготовлены для M8 privacy, out-of-sight rejection, protocol8, Runtime/client loss, meeting reconnect и guarded server SIGKILL; пока actualM8 acceptance этих suites отсутствует. Старые M7 results не переносятся. Explicit same-name late join — spectator policy, а не credential resume; signal dispatch сам по себе не доказывает process exit/cleanup.

## 47. Legacy regression

[Sandbox series](sandbox/summary.json) теперь содержит 4 actual case attempts: три accepted checks и один retained setup failure. Movement01 не исполнился из-за старого Development-only probe; новая non-Development opt-in попытка прошла13/13 prediction checks. Cooperation: same Completed calibration + обе Objective succeeded; Investigation: same Resolved/Restored/validated + обе Objective succeeded, normal natural clock. Все четыре distinct ordinary rooms, без моделей/CP mutation, finalcleanup0/inventory0. [Scene semantic audit](world/scene-semantic-audit.json):417documents/132transforms/78colliders, geometrychanged0; originalMainHall и SocialLayout unchanged.

## 48. Versions

Проверяемый M8 набор: Game 0.9.0, admission 4, Arena extension 0.6.0, Runtime 0.7.0; Core wire protocol 1.0. Отдельные manifests actor.standard 0.1.0 и actor.standard.communication 0.1.0 unchanged; это не версия admission или Core. [Wheel resources/hashes](runtime-package.json) и actual staging preflight согласованы с этим набором. Финальные clean client/server/CP stamps, immutable archives и published manifest ещё не приняты; dirty QA builds не объявляются distributable release.

## 49. GitHub commits

Текущий HEAD — baseline `4a4cbbd`; финального M8 source/evidence commit пока нет. Наличие local build-info или working-tree тестов не означает GitHub publication. PENDING: functional commit, точные artifact source stamps, отдельно обновлённые docs и clean final tree; приватные raw launch/SQLite/model reports в commit не включаются.

## 50. CI results

CI workflow расширен auth-free Runtime/SDK/helper/art/schema checks; Pillow13.0.0 закреплён только в CI, не production Runtime. Локальные PASS не являются GitHub Actions run для неопубликованного working tree. Нужны реальные ссылки и green status ControlPlane/SDK workflows на final functional commit и final HEAD; старые M7 workflows не закрывают M8.

## 51. VPS deployment

Живые M8 matches/resume/perf/Sandbox получены на изолированном staging, а не доказывают production activation. Новый deploy transaction имеет drain marker, verified nginx cgroup request barrier для старого CP, final serialized zero-room guard и rollback symlink/env600/unit/web; failed rollback сохраняет fail-closed marker. Эти source/local tests проверены независимо. Production M8 activation, actual rollback target behavior, health/version/checksums и HTTPS/release distribution ещё требуют отдельного gate; публичный HTTP нельзя назвать защищённым production transport.

## 52. Release artifacts

Публичны original editable art/generator/hash manifest, safe outcome JSON, actual screenshots и [verified Runtime0.7 wheel proof](runtime-package.json):545tests, retained wheel SHA256 `2aee36a2a60079ba56d53c954d40635b4b9524bf037f7eaa603765ffe0cdee5b`,206026bytes. Воспроизводимый package pipeline проверяет product inventory/stamps/RECORD/deps/archive links/Xbits, но final source-clean Unity+CP archives/release manifest/checksums/download ещё не сформированы. macOS Mono tested; Windows/IL2CPP release support не заявляется. Private contexts/logs/SQLite/credentials не входят в public release.

## 53. Known limitations

Открыты: минимум пяти strict independent Codex starts (сейчас 2), strict mixed provider proof, audibility/LOS sound revoke, полная screen×resolution/Agent HUD matrix, свежие M8 privacy/protocol/crash/lifecycle suites, UDP/RTT/GC/transition benchmark, final source/CI/production/HTTPS/distribution. Mixed02 tools null нельзя восстановить future collector fix. Witness02 fixture incomplete 2/5; victim01 complete 3/3 — deterministic Human QA, не Codex benchmark. Own Death/fold/dead-keyboard теперь actual, но sparse frames/DSP не доказывают smoothness или слышимость. Historical failures остаются; полная release-ready игра пока не заявляется.

## 54. Future ACTOR SDK roadmap

После закрытия текущей приёмки: развивать generic provenance/contradictions, bounded cognition/episode lifecycle, независимые provider diagnostics и developer-friendly capability registration. Arena roles/tasks/voting остаются game extension, resume credentials — вне Brain/context. Следующий adapter или более сложный интеллект не заменяют незакрытые release gates этой игры; вторую игру, matchmaking cluster, voice/RL/progression в M8 не добавляем.
