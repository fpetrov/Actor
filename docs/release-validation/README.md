# Milestone 8 — release validation

Рабочий отчёт по §78 задания. Снимок составлен только из сохранённых безопасных evidence; новые живые проверки добавляются после их завершения. **PENDING** означает обязательное доказательство, которое ещё не принято, а не PASS. Публичные материалы не содержат credentials, личные сообщения, prompts/reasoning моделей или скрытые факты активного матча. Independent model, mixed Human, deterministic headless, native rendered и offline/unit категории не смешиваются.

## 1. Overall completion status

Полный [реестр01–14](models/attempts-01-14.json) имеет **7 strictly accepted AI-vs-AI GameOver из14 зарегистрированных попыток**: основная серия01–12 дала6/12, отдельный controlled13–14 — один setup failure без моделей и один accepted GameOver, включая [пять08–12 с полным M8 binding](models/full-binding-08-12.json), и два full model-selected social cycle08/12. [Actual09–12](models/attempts-09-12-stream.json): 195 SDK turns, 28 interrupted с фактическими counters. [Headless suite](network/headless-m8-suite.json) закрыл шесть выбранных case groups с двумя сохранёнными отказами. [Native07](native-human/final-656-07.json) сохранил source656 nameplate/Discussion defects; [Native08 source72b](native-human/final-72b-08.json) завершил Human+5 peers и подтвердил bounded visible-label/six-life/six-card samples. Human создал meeting, но его ballot отсутствует в replay: Skip selection не выдаётся за acceptance. [Human03](human-mixed/attempt-03.json) прошёл default strict mixed, а [Human04](human-mixed/attempt-04.json) — strict38/38 с required Human vote: one accepted own Skip, actual meeting/chat и46 observed official turns; исход Crew/tasks, без kills/reports. Оба не доказывают full social cycle. [Controlled14](models/controlled-13-14.json) естественно завершился Crew/tasks без kills/reports/meetings/votes, поэтому не закрыл§61. **Полная release acceptance открыта**: controlled full social cycle§61, полная native matrix, оставшиеся fault/security/rollback gates. Текущий [clean C1/RC3](release-integrity-rc3.json) имеет [actual deployment17/17 + Sandbox13/13](deployment/activation-c1.json), [anonymous HTTPS7/7 assets](https-public-download-rc3.json) и [bounded native Fog comparison](fog/native-comparison-72b-c1.json). Исторические72b proofs сохранены; это не полная M8 release readiness. [Матрица](completion-matrix.json) разделяет категории.

## 2. Baseline и commits

[Baseline](baseline.json): `4a4cbbd46d8dcc8624699e405755a7d457138648`, Unity6000.6.4f1, URP17.6.0, NGO2.13.3. До M8: 560 Python tests (491 Runtime +69 scripts),96 subtests; SDK232, ControlPlane23;54 generated artifacts. Текущий source-bound artifact — [`ebb05bc4c315fbfb829addc313c328d69fa22e05`](https://github.com/fpetrov/Actor/commit/ebb05bc4c315fbfb829addc313c328d69fa22e05), C1/RC3. [Delta72b→C1](component-delta-72b-c1.json): единственный production change — client SocialFogRenderer;21 critical source components неизменны. [RC2/72b](release-integrity-72b.json) остаётся историческим baseline без retag. [Delta656→72b](component-delta-656-72b.json) фиксирует 5 presentation-only product changes и 21 unchanged component; старые actual tests остаются stamped исходными версиями. Clean packaging/draft integrity, native acceptance и production activation — разные gates.

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

Social мир использует authored floor union, layered walls и terminals без новых art colliders. Hall directory, Security instrument bank, Storage cargo и Engineering reactor принадлежат одной palette. [Static render](world/landmarks-static-world.json):125 sprites,12 original textures; [semantic audit](world/scene-semantic-audit.json) сохраняет legacy geometry. Native35e выявил error-magenta StaticSurface shader; [неуспех/remediation](world/shader-remediation.json) сохранены. Теперь [actual source656 Native07](native-human/final-656-07.json) показывает все четыре landmarks под fog в настоящем 800×600 Metal client, без error-magenta pixels по четырём saved frames. Это bounded shader/landmark sample, не все fog boundaries. [C1 native Fog comparison](fog/native-comparison-72b-c1.json) отдельно показывает Main Hall дугу менее гранёной на saved candidate frame:3 actual800×600 Metal frames, разные комнаты/роли/времена. Candidate002 — Hall, не дверь; точного serialized pose/camera join и temporal smoothing proof нет.

## 12. Lighting

Глубина и освещение в основном baked в original artwork; обязательных bloom и множества realtime lights нет. Реальный Metal/URP Renderer2D отрисовал сохранённый мир. Server interest и client fog остаются разными слоями: красивые стены не дают право показать скрытый actor. [C1 Fog samples](fog/native-comparison-72b-c1.json) подтверждают только bounded appearance: source/render change отделён от server interest. Полная M8 visual/audio проверка границ LOS, дверного traversal и temporal flicker не принята.

## 13. Audio

Есть 11 original short cues, 48 kHz PCM, deterministic source/licensing; Master управляет AudioListener, source применяет EffectsVolume без двойного умножения, есть headless/reduced-motion guards. [Victim presentation](native-human/victim-presentation.json) зарегистрировала ненулевой local DSP RMS/peak на ранних 0,042/0,183 s samples; поздние samples нулевые. Это подтверждает локальный sampled audio output, **не слышимость человеком**, не waveform coverage и не отсутствие других звуков за LOS. All-cue native listening и LOS sound revocation пока открыты; музыка не заявляется.

## 14. UI technology decision

Выбран один retained UI Toolkit overlay stack с общими tokens и view models. Игровые действия идут через существующие authoritative RPC/SDK; UI не определяет исход. Включён Noto Sans с локальным OFL-файлом; nameplate/wayfinding также переведены в retained labels с lifecycle cleanup.

## 15. Glassmorphism implementation

[Фактическая compatibility probe](ui-compatibility/accepted.json) подтверждает shipped blur shader на Metal/URP2D и tinted fallback. Первый неуспех [сохранён](ui-compatibility/attempt-01.json): runtime-created panel не удержал shader в build. После исправления edge energy 0,04783→0,000866; это проверка blur, не usability.

## 16. UI performance/fallback

В отдельной compatibility probe было по 90 frames на condition: mean fallback 10,55 ms, blur 8,33 ms; warmup/vsync не позволяют заключить, что blur быстрее. Fallback остаётся полноценным вариантом и отключается через settings. Есть [native layout samples](ui-integration/layout-validation.json) на четырёх размерах и [реальные gameplay frame windows](human-mixed/native-02.json). Отдельного A/B overhead всего integrated HUD, allocation/GC профиля и phase-transition spike benchmark пока нет.

## 17. Lobby redesign

Retained Lobby содержит обычные create/join/mode/settings и typed server errors. [Native layout](ui-integration/layout-validation.json) подтверждает 800×600, 1280×720, 1920×1080 и 2560×1080, тёмные buttons/popup, Escape/focus restore и Cyrillic paste. После mixed02 исправлен rematch capacity retry: только точный `server_capacity_reached`, 8 s cadence, максимум 75 s, без повторения неизвестного POST результата. [Witness02](native-human/witness-02.json) действительно создал новый round в том же process после bounded retry; старый failure сохранён. [Финальный extracted656 Lobby](release-integrity.json) и [тот же Lobby из скачанного GitHub draft](https-draft-download.json) отдельно проверены в native 800×600 windowed client. Это не room/gameplay acceptance. После actual deployment [public browser Create/Copy](deployment/web-browser-create-copy-72b.json) создал отдельный Ready0/8 Sandbox room: transient Copy receipt видел operator, но в saved JPEG его нет; clipboard roundtrip не проверялся. [Поздний GET404](deployment/web-room-retirement-72b.json) подтвердил natural retirement именно этого room, без remote signal. [Browser proof](web/browser.json) относится к веб-лобби, не Unity.

## 18. HUD redesign

HUD использует permitted own-state/public phase: life/alignment/duty, readiness, task state, actions/cooldowns и сообщения. Четыре пропущенных M8 SDK bindings исправлены;9 production-binding tests проверяют полный catalog, self hydration, TaskStep generation и private presentation. [Full-binding08–12](models/full-binding-08-12.json) подтверждает все4 capabilities в каждом из20 accepted Runtime sessions; за эти5 матчей сервер зарегистрировал26 Crew tasks. Mixed02 сохранил прежний clipped task-control defect; после fix [native witness02](native-human/witness-02.json) реально завершил calibration, sensor comparison и maintenance hold. Полная матрица Agent read-only HUD и всех размеров пока не закрыта.

## 19. Chat redesign

Composer/inbox различают player claim и pending/accepted/rejected transport receipt; server acceptance не означает прочтение Brain. В [native witness02](native-human/witness-02.json) UTF-8 512 bytes достиг ClientReceived, 514 bytes блокировались до send. Native chronology defect исправлен: общая временная лента, поздний ACK не переносит старое сообщение, own echo не дублируется; 7 production presenter tests проверяют порядок, TTL/redaction, focus и отмену autoscroll. Raw chat не публикуется. Финальная native проверка именно последнего chronology fix и IME composition ещё открыта.

## 20. Discussion screen

Model08/09/12 имеют реальные Discussion с тремя Codex senders в каждом: selected+succeeded typed Discuss, server receipts и recipient counters; только08/12 также закрыли model body-report gate. Mixed02 отрисовал Discussion1280×720. [Native07 Discussion800](native-human/final-656-07-discussion-roster-defect-800x600.png) сохранил clipped последнюю life row source656. В [fresh72b Native08](native-human/final-72b-08-discussion-six-life-800x600.png) все шесть name/life rows, включая длинное имя и Dead, видны в actual800×600 frame. Это bounded fix acceptance, не полная resolution/scroll/input matrix. Server acceptance не доказывает прочтение другой Brain.

## 21. Voting screen

Выбор и подтверждение разделены, финальный ballot решает сервер. Model08/09/12 имеют по 3 accepted Codex ballots и authoritative ejection;09 остаётся без body-report gate. Mixed02 дал 6 ballots в двух meetings, включая actor-bound Human ballot, при незакрытом provider gate. [Native08 Voting](native-human/final-72b-08-voting-six-cards-800x600.png) показывает все шесть cards и disabled Dead card. [Кадр Skip](native-human/final-72b-08-skip-selected-800x600.png) показывает **выбор до подтверждения**: оператор объявил попытку CastVote, но replay содержит только4 NPC ballots и **0 Human ballots**. Причина расхождения в Native08 не установлена; его Human ballot acceptance не заявляется. Отдельный [Human04](human-mixed/native-04.json) на том же72b затем подтвердил own Skip replay и [Your ballot is final](human-mixed/native-04-vote-final-800x600.png),38/38 strict checks с required Human vote; это новое доказательство, не исправление истории Native08. Полная keyboard/Skip/tie/read-only Agent matrix остаётся открытой.

## 22. GameOver screen

GameOver использует authoritative summary, чужой alignment раскрывается только после terminal. Strict01/08–12 имеют terminal agreement; mixed02 получил parity. [Native07 GameOver](native-human/final-656-07-gameover-800x600.png) завершился Crew/impostors_eliminated за754,679s. [Native08 GameOver](native-human/final-72b-08-gameover-800x600.png) завершился тем же естественным исходом за615,056s; Human survived/team won,5/5 fixture reports совпали с шестью server players. [Witness02](native-human/gameover-800.jpg) отдельно дошёл до900s match_timeout и затем через actual New Expedition создал [новый round](native-human/rematch-800.jpg): новые actor/match, Waiting, cleared assignment/summary. Это fresh rematch, не resurrection. Native07/08 сами не добавляют rematch proof.

## 23. UI screenshots

Есть native [Lobby/Settings/Unicode](ui-integration/layout-validation.json), [extracted656 Lobby](ui-integration/final-656-lobby-800x600.png), [GitHub draft Lobby](ui-integration/final-656-github-lobby-800x600.png), [mixed Voting](human-mixed/voting-800.png), [mixed GameOver](human-mixed/gameover-800.png), [Death](native-human/death-800.jpg) и [fold frames](native-human/victim-presentation.json). [Native07 record](native-human/final-656-07.json) сохраняет8 actual PNG/JSON pairs четырёх комнат, Waiting, обоих source656 defects и GameOver. [Native08 record](native-human/final-72b-08.json) добавляет6 pairs: [пять видимых nonoverlapping labels](native-human/final-72b-08-nameplate-sample-800x600.png), [unconfigured Agent HUD](native-human/final-72b-08-unconfigured-agent-hud-800x600.png), [шесть life rows](native-human/final-72b-08-discussion-six-life-800x600.png), [шесть Voting cards](native-human/final-72b-08-voting-six-cards-800x600.png), [Skip selection](native-human/final-72b-08-skip-selected-800x600.png), [GameOver](native-human/final-72b-08-gameover-800x600.png). PNG bytes сохранены; JSON меняет лишь screenshot basename. Все шесть simultaneous world nameplates не сняты. CUA JPEG остаются .jpg; старые [Tasks clipping](human-mixed/tasks-800-defect.png) и [shader35e failure](world/shader-failure-native-35e.jpg) сохранены. Completed [Human03 native record](human-mixed/native-03.json) добавляет actual [alive](human-mixed/native-03-alive-800x600.png), [own Death](human-mixed/native-03-death-800x600.png), [GameOver](human-mixed/native-03-gameover-800x600.png) PNG/JSON pairs. [Human04 final vote](human-mixed/native-04-vote-final-800x600.png) и [GameOver](human-mixed/native-04-gameover-800x600.png) — отдельные ended-game PNG/JSON; raw Discussion текст не публикуется. Полной screen×800/1280/1920 matrix нет.

## 24. Human usability results

[Mixed02](human-mixed/native-02.json) подтвердил native Ready/движение/chat/meeting/ballot, но strict provider proof incomplete. [Witness02](native-human/witness-02.json) прошёл четыре комнаты, три mechanics, report, UTF-8 bounds и fresh rematch; его fixture terminal proofs incomplete2/5. [Victim01](native-human/victim-01.json) проверил Death/dead-keyboard/parity, complete3/3. [Native07](native-human/final-656-07.json) complete5/5 с Human body report и native exit0, сохранил два source656 UI defects. [Native08 round3](native-human/final-72b-08.json) complete5/5 на72b: Human movement/Ready declarations, actor-bound CallMeeting, bounded corrected roster/nameplate frames; попытка Human Skip не стала accepted ballot. В [коротком Native08 round2](native-human/short-72b-08-round2.json) все5 fixtures технически завершили90s timeout, но intended meeting/screenshots не получены: operator coverage gap, не техническая ошибка. Итого четыре completed fixture games, из них три с отдельным native-input/gameplay proof; один старый witness incomplete. Это не Codex starts. По завершении следующих mixed cases integrator наблюдал нормальный CmdQ/exit0 того же owned Native08 app; это отдельное process-exit наблюдение, а не результат fixture supervisor. IME/audibility/full-resolution и attached Agent HUD matrix открыты; fullscreen не использовался.

## 25. Human + Agent multiplayer results

Собственный denominator — **4 попытки,2 strict PASS,2 сохранённых FAIL**. [Mixed01](human-mixed/attempt-01.json) — waiting_timeout/interrupted без Human readiness/gameplay proof. [Mixed02](human-mixed/attempt-02.json) реально завершился за659,645s:2kills,1report,2meetings,6ballots (один Human),6tasks,2sabotages; strict provider gate false из-за unknown interrupted-turn tool counts, которые не исправляются задним числом. [Human03 strict evaluation](human-mixed/attempt-03.json) прошёл37/37 checks: source72b,3 isolated official Codex participants,9closed SDK episodes,43turns/5interrupted, каждый с observed tool_calls0 и scope sdk_item_lifecycle. Общий матч завершился за230,017s Impostor/parity:2kills,4Crewtasks,1sabotage,0reports/meetings/ballots. [Native03](human-mixed/native-03.json) связывает own alive/death/GameOver frames с frozen Human terminal; Ready/move объявлены, Human killed/team lost. Human chat/report/vote/task не заявляются; extra require-human-vote/report gates не запрашивались. Это strict default mixed acceptance, **не** full social-cycle/§61 acceptance, не input attestation и не native process-exit proof. [Human04](human-mixed/attempt-04.json) затем прошёл38/38 с `--require-human-vote`:279,088s Crew/tasks_completed,8tasks,1Human emergency meeting,4ballots с одним own Human Skip;0kills/reports.15closed SDK episodes,46turns/3interrupted с observed tools0/sdk_item_lifecycle. [Native04](human-mixed/native-04.json) связывает accepted Human Skip с server replay и final-ballot UI; Ready/move/chat/meeting/vote объявлены. Receipt events2server/2client/0rejected не являются числом уникальных сообщений или прочтений Brain. Human survived/team lost; raw Discussion prose остаётся ignored. Это второй strict mixed PASS, без model kill/body-report/full-cycle/§61 proof.

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

Model08/09/12 имеют по 3 accepted Codex ballots, реальные meeting/Voting и server ejection. Mixed02 отдельно имеет 6 ballots в двух meetings и один actor-bound Human ballot; его strict provider gate false. [Human04](human-mixed/attempt-04.json) отдельно закрывает strict required-Human-vote gate: own Skip1 и4total ballots при реальном meeting, all3official providers complete; kills/reports в этом случае0. [Свежий protocol02](network/headless-m8-suite.json) на source656 прошёл все 8 owned-RPC negative/replay checks, включая final-ballot immutability и cancel-before-start-ACK, вместе с 4/4 authoritative Runtime completions и cleanup0. Protocol01 также имел 8/8 RPC, но только 3/4 completions и сохранён как whole FAIL. Исправление затронуло лишь optional fixture context, не Runtime/game authority.

## 33. Impostor deception evaluation

[Ручная postgame оценка08](models/attempt-08-disguise-review.json): grade `observed`, только attempted behavioral disguise. Модель сама выбрала 2 fake-task calls; 1 succeeded сопоставлен по selected/result position и installed typed completion contract, private output оставлен redacted. Отдельного fake-work server replay record нет. Это не доказательство убедительной лжи, смены чужого belief, false alibi/accusation или внутренней мотивации; нельзя назвать поражение/победу оценкой deception quality.

## 34. Results of complete live matches

[Семь strict accepted AI-vs-AI исходов](models/attempts-01-14.json);14 — отдельный controlled profile:

| Attempt | Время,s | Authoritative outcome | Kills | Tasks | Reports | Meetings | Ballots | Full cycle |
|---|---:|---|---:|---:|---:|---:|---:|---|
|01|141,554|Impostor/parity|2|0|0|0|0|нет|
|08|286,153|Crew/impostors_eliminated|1|6|1|1|3|да|
|09|288,414|Crew/impostors_eliminated|1|2|0|1|3|нет|
|10|137,881|Impostor/parity|2|4|0|0|0|нет|
|11|169,599|Crew/tasks_completed|0|8|0|0|0|нет|
|12|274,770|Crew/impostors_eliminated|1|6|1|1|3|да|
|14 controlled|302,373|Crew/tasks_completed|0|8|0|0|0|нет|

[08–12](models/full-binding-08-12.json) — 5 independent full-M8 starts;01 — historical condition. [Human03](human-mixed/attempt-03.json) отдельно прошёл default strict mixed acceptance, [Human04](human-mixed/attempt-04.json) — strict required-Human-vote; Mixed02 сохраняет неполный provider proof. Это отдельный3-model+Human denominator, не ещё один4-model independent start. Deterministic native victim01, [Native07](native-human/final-656-07.json) и [Native08 round3](native-human/final-72b-08.json) complete3/3,5/5,5/5; отдельный short Native08 round2 complete5/5 технически, без native-input acceptance; witness02 incomplete2/5. Ни один не добавляет Codex starts. **Отдельный controlled model-selected scenario§61 не принят**:14 дал valid model-selected task win,1sabotage и0accepted kill/report/meeting/ballots. При90s Discussion/60s Voting эти фазы вообще не возникли; модельные choices не подменялись.13 был setup failure до allocation/models. Ordinary full cycles08/12 и Human fixtures сохраняют собственные категории.

## 35. Number of attempts / failures / accepted runs

[Актуальный реестр01–14](models/attempts-01-14.json):14 зарегистрированных attempts,7 accepted GameOver,7failed,incomplete0, полный inventory. Основная серия [01–12](models/attempts-01-12.json) неизменна:6accepted/6failed (2setup,4Runtime),6strictstarts/2cycles. [Controlled13–14](models/controlled-13-14.json):2attempts,13setup failure(no local5080forward,no allocation/models),14one actual4Codex start и strictGameOver, но0fullsocialcycle. Для14 production zero-room GET использовал обычный publicorigin, staging остался тем же5081. Это не скрытый retry и не улучшение сценария за счёт исключения failures. [Full-binding08–12](models/full-binding-08-12.json) отдельно даёт5 starts с20 accepted full-capability Runtime sessions. [Mixed01–04](human-mixed/attempt-04.json) имеет собственный denominator4/strictPASS2/FAIL2; resume3 и Sandbox4 также считаются отдельно. Старые01–07/01–08 JSON сохраняют прежние snapshot values. [Continuation conditions](models/attempts-09-12-conditions.json): Brain limit1,5m/warning800k, participation3m,900s match/1100s bound; actual failures не исключаются из общего реестра.

## 36. Codex usage

[Актуальный реестр01–14](models/attempts-01-14.json) содержит known detailed usage357 SDKturns: input11 275 132 (cached8 249 984), output71 387,total11 346 519tokens.16 reports имеют неполные turn/tool/usage сведения,12registeredparticipants не имеют Runtime report (включая setup failures); известный tools0 не переносится на unknown. Participation measured_tokens=18 550 876 — другой счётчик. Исторический [01–12](models/attempts-01-12.json) остаётся прежним snapshot. [Controlled14](models/controlled-13-14.json) отдельно:66turns/8interrupted,observed tools0/scope sdk_item_lifecycle,input3 355 594/cache2 767 616/output9 073/total3 364 667. Usage взят из validated Brain episode aggregates, не сложением cumulative SDKturn snapshots. [Actual09–12](models/attempts-09-12-conditions.json):195 turns, input5 203 251 (cached3 608 960), output38 872, total5 242 123. Mixed02 отдельно:110 reported turns /5 140 713 total, interrupted tool counts null. [Human03](human-mixed/attempt-03.json) отдельно:43 SDK turns/5interrupted, input1 471 220 (cached1 093 376), output6 456, total1 477 676; все43 actual tool counters0. Это не прибавляется к реестру AI-vs-AI01–12. Human04 отдельно:46 SDK turns/3interrupted, total1 240 198 tokens; всеactualtoolcounts0. Ни один mixed case не прибавляется к AI-vs-AI denominator. Cost неизвестна; dollar estimate не придумывается.

## 37. Runtime/Skill changes

Runtime добавляет typed discussion, bounded pursuit, context-sensitive Skill catalog, разрешённую working knowledge и generic lifecycle guards; Core не знает Arena roles/tasks/voting. Terminal ledger раскрывает только зарегистрированные Skill IDs без args/outputs. Official single-stream collector сначала проверен [synthetic SDK parity](tests/sdk-stream-parity.json), затем [actual09–12](models/attempts-09-12-stream.json):195 unique SDK turns,28 interrupted с фактическим lifecycle coverage, tool counters0 для всех195. [Controlled14](models/controlled-13-14.json) отдельно подтвердил66actualturns/8interrupted/allobserved0 в4closed official providers и все4новыхM8bindings в каждом accepted session. Это новое доказательство не восстанавливает утраченные counters mixed02 или старых failures. Collector/provider lifecycle и game authority проверяются раздельно.

## 38. Navigation improvements

Social navigation использует публичный authored graph, fresh own pose, bounded reroutes и остановку по distinct samples. Pursuit учитывает форму capsules, выбирает current visible/датированное last-seen и не вызывает kill автоматически. В accepted08–12 movement/approach входят в реальные task/report/kill sequences; это не отдельный stress test всех crowded routes. Sandbox [movement retry](sandbox/movement-attempt-02.json) прошёл13 actual synthetic prediction checks, включая wall slide/reversal/reconciliation; native input latency из него не выводится.

## 39. Memory/belief/provenance changes

Working knowledge различает personal observations, player claims и hypotheses. Citation snapshot фиксирует content/time/provenance на момент вывода; latest slot не переписывает старое основание. Protected dependency group устанавливается/evicts атомарно, aliases не становятся независимыми источниками. Typed owner payloads имеют match/recipient/expiry guards; private result не попадает в публичную память по умолчанию. Source/wheel tests подтверждают эти contracts, но не истинность чужого claim или наличие человеческого reasoning.

## 40. Reconnect/rematch

[Credential transport resume](network/reconnect.json): 3 supervisor attempts = 2 setup failures + 1 accepted recovery. Тот же process пережил 30,094 s bidirectional UDP blackhole, сохранил actor/match/private assignment/alive, поднял generation, отверг connected duplicate и old-generation intent; 121 stationary sample / 2,004 s, cleanup0. [Отдельный fresh-ticket late join](network/headless-m8-suite.json) на source656 наблюдался во время настоящей Discussion: 32 samples / 2,325 s, unassigned/passive, commands0, displacement0; прежний actor необратимо Disconnected. Это новый spectator, не credential resume или resurrection. Post-death resume/ghost-vulnerability kill/native reconnect не заявлены. [Native witness02](native-human/witness-02.json) создал новый round в том же process с новыми actor/match и cleared assignment/summary.

## 41. Client FPS

[Fog before/after](fog/native-comparison-72b-c1.json): baseline72b115,767FPS/p959,253ms за38,975s; candidateC1118,960FPS/p959,015ms за25,000s. Второй C1 Hall sample119,188FPS/p959,112ms за38,058s. Все recorded frames focused,800×600 windowed Metal; длительности/роли/сцены различаются. Это не formal GPU benchmark, причинный speedup, matched-pose trial или zero-overhead proof.

[Native02 v2 frame windows](human-mixed/native-02.json) измерены в обычном Metal standalone после 2 s warmup: Exploration 800×600 — 9 565 focused frames / 79,87 s, 119,76 fps, p95 9,10 ms; Discussion 1280×720 — 4 863 / 40,60 s, 119,78 fps; Voting 800×600 — 2 179 / 18,17 s, 119,89 fps; GameOver800 — 1 682 / 14,02 s, 120,00 fps. Это Unity rendered-loop intervals, не hardware presentation/physical input latency. Discussion800 имеет 0 samples и не считается замером; все phases×resolutions и transition/kill/GC spikes ещё не закрыты.

## 42. Server performance

[Postgame instrumentation](performance/human-mixed-02-postgame.json): 704,914 s от Social initialization до GameOver, включая Waiting; process CPU 8,52% одного logical core, process-lifetime peak147,33 MiB, FixedUpdate50,00/s, Update59,77/s. Visibility p95/max0,2232/11,5315 ms, refresh3,6041/38,2142 ms; p95 только latest1024, maxima all recorded. Это не NGO network tick. Отдельный [15,123 s Exploration sample](performance/mixed-server-03.json) дал CPU9,65%, RSS145,88→146,48 MiB,27threads,UDPdrop delta0; одно короткое окно не доказывает sustained maximum.

## 43. Network performance

Полный postgame counter насчитал 7 452 585 bytes / 34 139 сообщений **только instrumented Social protobuf**; anonymized recipient aggregates опубликованы без IDs. Они исключают transforms/spawns/Standard communication/RPC/NGO/UDP headers и не являются total UDP throughput. 15 s collector мерил socket queues/drops, не packets/throughput/RTT. Resume packet drop counters относятся к injected blackhole. Актуальные M8 full-bandwidth/bridge RTT и сопоставимый M7 baseline benchmark пока отсутствуют; display FPS не выводится из headless rate.

## 44. Automated tests

[Runtime source545/545 и fresh installed wheel545/545](runtime-package.json) PASS outside checkout без SDK/model auth;38Skills/14Brains/35Rules/1profile. [C1 exact-source RC3 record](release-integrity-rc3.json) фиксирует SDK302/302 и ControlPlane29/29 и два green CI; Runtime wheel bytes unchanged, новая C1 wheel installation не заявляется. [Прежний source656 snapshot](tests/final-dotnet.json)288/29 и72b nameplate14 cases сохранены в своих scopes. [Offline snapshot](tests/final-offline.json): scripts124+96subtests, отдельная пересекающаяся helper/art group153+282,web9,schema54; optional protocol fixture14 отдельно. Разные runs не складываются в invented total, CI не является native/model acceptance.

## 45. Native rendered tests

[Layout proof](ui-integration/layout-validation.json) покрывает Lobby/Settings на четырёх размерах. [656 extraction/download](https-draft-download.json) достиг actual Lobby; короткое окно не gameplay benchmark. [Native07](native-human/final-656-07.json) связывает clean656 Human executable с four-room fog/landmark samples, report и GameOver, сохраняя оба visual FAIL. [Native08](native-human/final-72b-08.json) связывает actual clean72b executable с6 saved800×600 frames: пять видимых labels без observed overlap и с ellipsis, все6 life rows/cards, selected Skip и GameOver. **Не** заявлены все6 simultaneous labels или accepted Human ballot. Unconfigured Agent HUD показывает Connection unavailable; при700ms W own authoritative delta0, но Runtime/model не attached и recovery не проверен. [Mixed02](human-mixed/native-02.json), [strict Human03 alive/death/terminal](human-mixed/native-03.json), [Human04 accepted Skip/terminal](human-mixed/native-04.json), [witness02](native-human/witness-02.json), [victim01](native-human/victim-01.json) остаются отдельными scopes. [Shader35e failure](world/shader-remediation.json) сохранён. [C1 Fog native record](fog/native-comparison-72b-c1.json) добавляет3 unchangedPNG/JSON pairs: [baselineHall](fog/baseline-72b-main-hall-001-800x600.png), [candidateHall001](fog/candidate-c1-main-hall-001-800x600.png), [candidateHall002](fog/candidate-c1-main-hall-002-800x600.png). Оба peer workflows завершились90s deadline; candidate Human был остановлен180s lifetime(exit-15), не normalexit. Cleanup0 подтверждён; поздние own-room404 — отдельные receipts, не исправление workflowFAIL. All-cue audibility, attached Agent HUD и полная screen×resolution matrix открыты.

## 46. Fault tests

[Actual headless suite](network/headless-m8-suite.json): **8 завершённых case attempts, 6 accepted, 2 failed**, 7 с выполненной процедурой. Source35e: privacy01 strict final PASS, runtime-loss02 PASS, client-loss01 PASS; protocol01 whole FAIL (8 RPC PASS, 3/4 Runtime terminal), runtime-loss01 preflight FAIL (0 clients/Runtime, local bridge address in use). Retry02 явно использовал тот же ещё не заселённый room. Privacy helper первоначально exit1 ожидал server postgame evidence; после получения этого evidence strict offline projection exit0 без второго gameplay launch. Source656: protocol02 whole PASS, Discussion late join01 PASS и guarded Sandbox server SIGKILL01 PASS. [Provenance](network/headless-m8-provenance.json) разделяет exact component stamps; wire/Runtime/authority sources между35e/656 неизменны, shader/native acceptance не переносится. Runtime loss дал наблюдаемый stopped/keyboard-disabled живой client; client-loss→GameOver занял 60,491 s. Server loss дал actual original-process exit, 10 guard + 10 lifecycle checks, stale→terminal 29,521 s, commands0→0: это waiting actor, не moving-loss latency benchmark. На handoff 65 recorded owned PIDs / 7 groups проверены, remaining0, rooms0; optional protocol plugin удалён. [Credential resume](network/reconnect.json) имеет свой denominator. Deployment21 — local transaction tests. Late messages, post-GameOver actions, death during Skill, meeting during actual Codex turn и body/presentation replay после resume этим suite не заявлены. Retrospective interrupted-turn counts и episode_superseded без общего clock join не доказывают, что death/meeting застали именно active SDK turn; эти§65gates остаются открытыми.

## 47. Legacy regression

[Sandbox series](sandbox/summary.json) теперь содержит 4 actual case attempts: три accepted checks и один retained setup failure. Movement01 не исполнился из-за старого Development-only probe; новая non-Development opt-in попытка прошла13/13 prediction checks. Cooperation: same Completed calibration + обе Objective succeeded; Investigation: same Resolved/Restored/validated + обе Objective succeeded, normal natural clock. Все четыре distinct ordinary rooms, без моделей/CP mutation, finalcleanup0/inventory0. [Scene semantic audit](world/scene-semantic-audit.json):417documents/132transforms/78colliders, geometrychanged0; originalMainHall и SocialLayout unchanged.

## 48. Versions

Версии unchanged: Game0.9.0/admission4/Arena0.6.0/Runtime0.7.0/Core1.0, actor.standard/communication0.1.0. Текущий [C1/RC3 artifact](release-integrity-rc3.json) имеет три clean `ebb05bc` stamps и non-Development Unity options. [Delta72b→C1](component-delta-72b-c1.json) — один client Fog renderer,21 unchanged committed components; это не binary equivalence. [72b RC2](release-integrity-72b.json),35e/656 headless/native/model proofs сохраняют исходные stamps. Native08/Human03–04/Controlled14 не выдаются за новые C1 model runs. Retained Runtime wheel545 proof и authored Skill bytes unchanged.

## 49. GitHub commits

Artifact source — [`ebb05bc4c315fbfb829addc313c328d69fa22e05`](https://github.com/fpetrov/Actor/commit/ebb05bc4c315fbfb829addc313c328d69fa22e05). Timeline:35e shader failure→656 shader remediation/Native07 layoutFAIL→72b bounded presentation fix→C1 client Fog smoothing. [72b→C1 source audit](component-delta-72b-c1.json) сохраняет старые execution scopes. Следующий commit содержит только docs/evidence и не ретаргетирует immutable C1 archives. Raw launch/SQLite/model context не публикуются; evidence HEAD и artifact source различаются.

## 50. CI results

Exact C1 source имеет два green workflows: [ControlPlane39715858801](https://github.com/fpetrov/Actor/actions/runs/39715858801) и [SDK+Runtime39715858807](https://github.com/fpetrov/Actor/actions/runs/39715858807), [source binding](release-integrity-rc3.json). Это coordinator-confirmed completed metadata, отдельно от actual native/model/deploy receipts. Прежние72b CI сохранены в [RC2 integrity](release-integrity-72b.json),656 — в [RC1](release-integrity.json). CI проверяет auth-free deterministic tests, не заменяет gameplay/GUI/performance acceptance. Pillow13.0.0 — tooling dependency. Будущий evidence-only commit не новая версия продуктов.

## 51. VPS deployment

[C1 actual activation](deployment/activation-c1.json) завершилась exit0 для release `20261022T033223Z-ebb05bc`; [final provenance](deployment/provenance-c1.json)17/17. Inventory:CP343 entries,raw server217 против packaged216 плюс generated DontShip marker,website8. [Fresh public Sandbox smoke](deployment/sandbox-smoke-c1.json)13/13: ordinary NGO owner bind/occupied heartbeat,active23,096s/total84,932s,own room natural404,owned clients/groups0. Это headless synthetic owned-input scope, **без Runtime Hello/Brain, physical keyboard/display или Social acceptance**.

[Историческая72b activation](deployment/activation-72b.json) сохранила первоначальный16/17FAIL из-за старого.DS_Store, recoverable quarantine1 и последующий17/17PASS; это не выполненный rollback. Её отдельные [browserCreate/Copy](deployment/web-browser-create-copy-72b.json) и [own-room retirement](deployment/web-room-retirement-72b.json) также не retagC1. [Поздний candidate Fog room404](deployment/fog-native-room-retirement-c1.json) не доказывает source сервера или all-room emptiness. HTTP website/CP иUDPgameplay limited-test, без TLS/confidentiality/continuous-availability/actual rollback claim.

## 52. Release artifacts

Текущий [clean C1 RC3 integrity](release-integrity-rc3.json):5product artifacts/7distribution files/6checksum entries, три rebuilt native products bound `ebb05bc`; retained Runtime wheel и authored Skill unchanged. [`v0.9.0-rc.3`](https://github.com/fpetrov/Actor/releases/tag/v0.9.0-rc.3) опубликован как prerelease(draft=false). [Actual anonymous HTTPS verification](https-public-download-rc3.json):7/7assets/127653941bytes,6/6SUMS,9HTTP200/SSL0 receipts, exact tag/source and GitHub digests. Systemcurl использовал ordinary OS trust, без auth/cookie/proxy/netrc/customCA/TLS bypass. [RC3 denominator](https-public-download-rc3-attempts.json):1actual download PASS/0failed; один offline expectation correction отдельно, без HTTP. Это не gameplay/model denominator.

[RC2 public proof](https-public-download.json) остаётся historical:4download attempts/3setupFAIL/1accepted, все old bytes unchanged; RC1 остаётся superseded unpublished. Wheel0.7 unchanged545/545,SHA256 `2aee36a2a60079ba56d53c954d40635b4b9524bf037f7eaa603765ffe0cdee5b`,206026bytes; fresh C1 install не заявляется. GitHub HTTPS не является Apple notarization, Windows/IL2CPP или TLS игрового HTTP/UDP. Bounded Fog samples не превращают limited-test RC3 в full M8 acceptance.

## 53. Known limitations

Закрыты5 independent full-binding starts08–12, actual collector09–12, выбранные [headless cases](network/headless-m8-suite.json), [native656 four-room shader samples](native-human/final-656-07.json), [bounded72b roster/nameplate samples](native-human/final-72b-08.json), [default strict Human03](human-mixed/attempt-03.json) и [required-Human-vote Human04](human-mixed/attempt-04.json), clean C1 package/green CI, actual limited-test [C1 production activation/Sandbox smoke](deployment/activation-c1.json), [anonymous public RC3 HTTPS](https-public-download-rc3.json) и [bounded Fog appearance](fog/native-comparison-72b-c1.json). Отдельный [controlled13–14](models/controlled-13-14.json) выполнен, но не закрыл social cycle: setup13 сохранён,14taskwin без kill/report/meeting/vote. Открыты controlled full social cycle§61, все6 simultaneous world labels, полная screen×resolution/attached Agent HUD matrix, IME/audibility/LOS audiovisual revoke, оставшиеся§65 live faults, UDP/RTT/GC/transition benchmark и actual rollback/continuous-availability/security gates. Production HTTP/UDP не имеет TLS/confidentiality proof, Apple notarization/Windows также отсутствуют. Headless privacy не native audiovisual proof; waiting-actor server loss не moving-input stop latency. Mixed02 tool counts unknown; Human03 accepted отдельно без report/Discussion/Voting, Human04 — с meeting/vote/chat, но без kill/report. Native08 прежний attempted Skip остаётся без replay, несмотря на accepted Skip в последующем Human04. Native07 сохраняет оба656 UI FAIL; Native08 bounded corrections не закрывают полный UI gate. Три native-input fixture games плюс один технически завершённый short UI attempt не являются модельным benchmark. Fog evidence не содержит matched serialized pose, Door traversal, temporal flicker, GPU benchmark или successful operator-stop workflow; late404 не переписывает deadlines. Полная release readiness не заявляется.

## 54. Future ACTOR SDK roadmap

После закрытия текущей приёмки: развивать generic provenance/contradictions, bounded cognition/episode lifecycle, независимые provider diagnostics и developer-friendly capability registration. Arena roles/tasks/voting остаются game extension, resume credentials — вне Brain/context. Следующий adapter или более сложный интеллект не заменяют незакрытые release gates этой игры; вторую игру, matchmaking cluster, voice/RL/progression в M8 не добавляем.
