# ACTOR Broadcast: исследование исходного кода

Дата исследования: 2026-10-23. Источник: локальная рабочая копия `/Users/fpetrov/PycharmProjects/Actor`. Исследование только чтением. Исходный репозиторий не изменялся; git fetch/pull, установка, тестовый запуск, OAuth, загрузка моделей, OBS и Twitch не выполнялись. Этот отчет сохранен вне репозитория.

Все относительные пути ниже отсчитываются от `/Users/fpetrov/PycharmProjects/Actor/`. Номера строк относятся к прочитанной локальной копии. Другой агент может в это время обновлять копию, поэтому для долговременных ссылок желательно закрепить commit, который установит основной исследователь.

## Главные выводы

1. Трансляция реализована как отдельный наблюдатель, а не дополнительный Crew/Impostor. Наблюдатель не получает персонажа, collider, игрового слота, права на действия игрока или участие в определении победы.
2. Комментатор не знает весь серверный мир. Сервер сначала строит разрешенный `BroadcastView`; локальная LLM получает только факты, показ которых подтвержден настоящим native кадром.
3. Пользовательское «kvn», вероятнее всего, относится к **Qwen**. В текущем коде default TTS — **Qwen3-TTS-12Hz-1.7B-CustomVoice BF16**, локально через **MLX Audio**, голос **Ryan**, русский язык. Kokoro в исследованном исходном дереве и документации не найден.
4. Текст — официальный Codex SDK, модель **gpt-6-luna**. Обычный комментарий использует минимальный подтвержденный effort, в измеренном каталоге `low`. Один ограниченный анализ публичных аргументов может запросить `medium`.
5. Публичный Twitch запуск уже описан как реально состоявшийся, однако текущий приемочный прогон остается FAIL из-за ограничения ресурсов. Не следует представлять всю подсистему как принятую для длительного автономного эфира.

Основания: `docs/broadcast.md:21`, `docs/broadcast.md:31`, `docs/broadcast.md:58`, `docs/broadcast.md:94`, `docs/broadcast.md:134`, `docs/broadcast.md:336`; `runtime/actor_broadcast/speech.py:19`; `Agents Arena/Assets/Scripts/Networking/MatchSession.cs:340`.

## Полная цепочка данных

```text
Control Plane / внутренний operator endpoint
  -> подписанный одноразовый observer ticket
  -> Unity authoritative game server
  -> server-side BroadcastViewProjector: radius + viewport + LOS + public fields
  -> NGO reliable named messages, observer sampling 30 Hz
  -> отдельный windowed Unity observer: camera + proxies + fog + public HUD
  -> end-of-frame BroadcastPresentationAck
  -> BroadcastGameAdapter -> ActorBridgeClient / GrpcActorTransport
  -> Python BroadcastBridge -> ArenaBroadcastProjector -> PresentedView
  -> EvidenceLedger + SpokenLedger + CommentaryScheduler
  -> CodexCommentaryProvider: gpt-6-luna -> validated complete clauses
  -> persistent MLX Qwen BF16 TTS worker over typed socketpair IPC
  -> 24 kHz mono Float32 little-endian PCM
  -> loopback BrowserAudioSink -> OBS Browser Source / AudioWorklet at 48 kHz
  -> OBS scene: exact observer-window video + browser voice/subtitles
  -> Twitch ingest

Optional Twitch chat:
Twitch EventSub -> sanitize/deduplicate/attribute -> viewer claim
  -> same bounded evidence/scheduler/provider/speech path
```

Важное уточнение: у `ActorBridgeClient` задана `ObservationRateHz = 10`, но adapter также публикует события подтвержденного кадра. Не стоит сводить всю цепочку к одному значению FPS. Серверный observer sampling 30 Hz, authoritative social refresh/private player feeds 10 Hz и OBS encoding FPS — разные частоты.

Якоря:

| Участок | Код |
| --- | --- |
| Внутренний admission | `src/Actor.ControlPlane/Api/Endpoints.cs:49`, `src/Actor.ControlPlane/Api/BroadcastOperatorBoundary.cs:8`, `src/Actor.ControlPlane/Application/LobbyManager.cs:210` |
| Python admission client | `runtime/actor_broadcast/admission.py:32`, `runtime/actor_broadcast/admission.py:104` |
| Unity admission без PlayerObject | `Agents Arena/Assets/Scripts/Networking/MatchSession.cs:340`, `Agents Arena/Assets/Scripts/Networking/MatchSession.cs:345` |
| Единственный server wire projector | `Agents Arena/Assets/Scripts/Networking/BroadcastViewProjector.cs:9`, `Agents Arena/Assets/Scripts/Networking/BroadcastViewProjector.cs:92`, `Agents Arena/Assets/Scripts/Networking/BroadcastViewProjector.cs:205` |
| NGO wire delivery и подтверждения | `Agents Arena/Assets/Scripts/Networking/BroadcastSession.cs:158`, `Agents Arena/Assets/Scripts/Networking/BroadcastSession.cs:181`, `Agents Arena/Assets/Scripts/Networking/BroadcastSession.cs:195`, `Agents Arena/Assets/Scripts/Networking/BroadcastSession.cs:227` |
| Реальный кадр | `Agents Arena/Assets/Scripts/Presentation/BroadcastViewPresenter.cs:355`, `Agents Arena/Assets/Scripts/Networking/BroadcastSession.cs:276` |
| gRPC bridge | `Agents Arena/Assets/Scripts/Actor/BroadcastClientController.cs:93`, `Agents Arena/Assets/Scripts/Actor/BroadcastGameAdapter.cs:21`, `runtime/actor_broadcast/bridge.py:9` |
| DTO projection и scheduler wiring | `runtime/actor_broadcast/arena.py:15`, `runtime/actor_broadcast/native.py:451`, `runtime/actor_broadcast/native.py:568` |
| Text provider | `runtime/actor_codex/commentary.py:201`, `runtime/actor_codex/commentary.py:397` |
| TTS | `runtime/actor_broadcast/speech.py:19`, `runtime/actor_broadcast/speech.py:161`, `runtime/actor_broadcast/tts_worker.py:66` |
| Browser audio output | `runtime/actor_broadcast/audio.py:259`, `runtime/actor_broadcast/audio.py:395`, `runtime/actor_broadcast/resources/audio-worklet.js:4` |
| OBS composition/publication | `runtime/actor_broadcast/obs.py:246`, `runtime/actor_broadcast/obs.py:426`, `runtime/actor_broadcast/obs.py:800` |

## Наблюдение, приватность и доказательства

`BroadcastView` — отдельное protobuf extension сообщение, не новый игровой transport. Оно содержит scope, sequence, semantic revision, source timestamp/expiry, camera, публичную фазу, task progress, видимых actors/bodies, разрешенный meeting/vote result, editorial facts и terminal result. Данные итоговых ролей разрешены только в GameOver. `BroadcastActor` не несет alignment, private task truth, cooldown, план или память.

Сервер выполняет геометрическую фильтрацию ДО отправки. Проверяются radius, orthographic viewport и LOS; косметические proxies не имеют gameplay authority. Убийство вне обзора не должно порождать событие, счетчик, camera cut или semantic revision. Найденное тело само по себе не раскрывает убийцу. `attack-witnessed` требует независимого pre-action visible witness.

Особая сильная сторона: получение wire frame не равно его показу. Есть как минимум четыре разных подтверждения:

- transport receipt освобождает окно reliable delivery;
- presentation ACK доказывает coherent native camera/proxies/fog/HUD в завершенном non-headless кадре;
- synthesis terminal сообщает фактическое окончание генерации аудио;
- playback ACK приходит из AudioWorklet после потребления samples локальным audio graph.

Ни одно из этих подтверждений не доказывает, что зритель Twitch услышал звук. Для итогового routing используется отдельная запись OBS; viewer-audibility требует отдельной проверки.

`BroadcastPresentationRules.ForCognition` оставляет только точные версии фактов, разрешенные ACK. Текущий position set заменяется, а не объединяется со старыми позами. Headless режим не может создать presentation ACK. Исторический факт может сохраниться в пределах retention, не превращаясь в текущую позицию или объективную истинность. Claims игроков и viewer chat остаются claims.

Якоря: `proto/extensions/agents_arena/v1/broadcast.proto:88`; `Agents Arena/Assets/Scripts/Networking/BroadcastViewProjector.cs:132`; `Agents Arena/Assets/Scripts/Networking/BroadcastViewProjector.cs:140`; `Agents Arena/Assets/Scripts/Networking/BroadcastSession.cs:259`; `Agents Arena/Assets/Scripts/Networking/BroadcastPresentationRules.cs:27`; `Agents Arena/Assets/Scripts/Networking/BroadcastPresentationRules.cs:85`; `runtime/actor_broadcast/contracts.py:1`; `runtime/actor_broadcast/arena.py:21`.

## Камера и редакторская логика

Камерой управляет локальный детерминированный `CameraDirector`, а не LLM и не скрытые серверные события. Он работает над свежим реально показанным публичным view. Доступен bounded selector до шести публичных identities/names: без позиции, room, life или role. Его membership сохраняется при смерти, disconnect и потере LOS, поэтому selector не служит side channel. Для selection нужен отдельный `camera_targets_presented` ACK читаемого списка.

Каждый подтвержденный camera focus получает один случайный dwell 18–26 секунд; выбирается least recently watched разрешенный identity. Сервер проверяет собственный minimum dwell 8 секунд. Camera command accepted еще не значит camera presented: подтверждение только по новому matching presented epoch. Discussion/Voting/GameOver фиксируют камеру. Пустой свежий view может инициировать поиск после 10 секунд на камере и 5 секунд непрерывной наблюдаемой пустоты. Stale/missing frames не равны пустоте.

Чтобы длинный рассказ не блокировал rotation, scheduler резервирует настоящую clause boundary, отменяет остаток старого focus, ждет FlushAck и только затем пропускает camera request. P0/P1 события имеют приоритет. Сервер сглаживает canonical camera eye до геометрической фильтрации; client использует именно этот eye, а не собственную непроверенную интерполяцию.

Якоря: `runtime/actor_broadcast/director.py:40`, `runtime/actor_broadcast/director.py:74`, `runtime/actor_broadcast/director.py:187`; `runtime/actor_broadcast/scheduler.py:328`; `runtime/actor_broadcast/native.py:600`; `Agents Arena/Assets/Scripts/Networking/BroadcastViewProjector.cs:67`, `Agents Arena/Assets/Scripts/Networking/BroadcastViewProjector.cs:100`; `docs/broadcast-editorial.md:7`.

## Комментатор, память, частота вызовов

Это отдельный narrator capability policy и bounded WorkingKnowledge scope. Player memory database и gameplay policy не импортируются. `EvidenceLedger` хранит неизменяемые exact id/version refs, original observation time/expiry и provenance. Hypothesis/contradiction notes зависят от источников; revoke/expiry каскадно удаляет зависимые notes и pending speech. Две версии одного claim не становятся двумя независимыми свидетелями.

`SpokenLedger` считает тему освещенной только по actual completed playback. Generated text, synthesized text и interrupted prefix не равны услышанной фразе. Без word alignment partial playback остается consumed sample range; система не выдумывает точный услышанный текстовый префикс.

`CommentaryScheduler.observe()` — bounded local code. Он coalesce события, использует priority, cooldown и один active provider. Transform ticks, chat flood и пустой recap не запускают запрос на каждый update. Default quiet interval — 20 секунд с ±25% jitter, то есть 15–25 секунд; sample один на текущий quiet anchor. Реактивный текст целится в 2–3 clauses / 25–45 слов, recap/callback — примерно 3 clauses / 35–55 слов. Это мягкие цели, не padding requirement. Одна clause предпочитает до 16 слов.

Один persistent SQLite BudgetLedger делят ordinary/reactive, idle, callback, analysis, repairs, retries/cancellations. Reservation до provider call, reconcile по actual unique SDK usage; unknown usage не равен нулю; restart/new match не сбрасывает hourly quota. Default maximum: 1 параллельный LLM call, 2 pending speech segments, 12 секунд aggregate pending speech, 45 секунд provider timeout. В конфигурации есть 180 hard calls/hour, 60/match, 600000 input tokens/hour и 60000 output/hour; на основной слайд эти внутренние лимиты лучше не выносить.

Текстовый provider использует отдельный Codex home с официальным ChatGPT sign-in, exact gpt-6-luna и pinned SDK 0.162.1. Tools отключаются capability policy и проверяются на SDK item lifecycle; shell/MCP/player tooling не являются частью narrator. Structured output содержит короткие segments с exact evidence refs и до двух notes. Парсер пропускает только полностью закрытые проверенные speech objects из final-answer, не reasoning, tools, fragments или diagnostics. Thread rotation bounded; model/account availability проверяется до эфира, paid API fallback отсутствует.

Анализ встречи — один тот же provider turn с `medium` effort при поддержке, максимум 1 на meeting / 3 на match и внутри общего budget. Он не запускает mini-classifier или дополнительную модель. Игровой classifier — другая подсистема ACTOR, не часть broadcaster admission.

Якоря: `runtime/actor_broadcast/evidence.py:14`, `runtime/actor_broadcast/evidence.py:105`; `runtime/actor_broadcast/spoken.py:21`, `runtime/actor_broadcast/spoken.py:43`, `runtime/actor_broadcast/spoken.py:128`; `runtime/actor_broadcast/scheduler.py:1`, `runtime/actor_broadcast/scheduler.py:187`, `runtime/actor_broadcast/scheduler.py:440`, `runtime/actor_broadcast/scheduler.py:583`; `runtime/actor_broadcast/config.py:9`, `runtime/actor_broadcast/config.py:32`; `runtime/actor_broadcast/budget.py:38`; `runtime/actor_codex/commentary.py:170`, `runtime/actor_codex/commentary.py:289`, `runtime/actor_codex/commentary.py:397`; `docs/broadcast-editorial.md:68`.

## Qwen, аудио и OBS

Точная модель: `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16`, pinned snapshot `52f4770fd9726457eae3d3b6aa92047a25a10776`. Worker проверяет metadata и BF16/F32 dtypes weights, наличие русского и Ryan, отсутствие quantization. Runtime dependencies отдельно от base ACTOR: mlx-audio 0.6.1, mlx 0.32.4. Worker загружается и прогревается один раз; один generation thread, bounded socketpair frames. На выходе 24000 Hz, mono, f32le.

`BrowserAudioSink` проверяет scope/utterance epoch, chunk ordering, exact frame offset, actual duration cap, route health, evidence validity и expiry до synthesis/chunk/playback. Ring bound — 72000 source samples. AudioWorklet непрерывно ресемплирует 24 -> 48 kHz с FIR/history между chunks. Cancellation имеет отдельные emission-stop и real render flush подтверждения. Нет микрофона, webcam, desktop capture или общего system audio; game audio опционален и по умолчанию выключен.

OBS имеет owned profile и collection `ACTOR Broadcast`, четыре сцены Preparing/Match/Ending/Safe. Видео привязано к точным window ID/PID/geometry, не title search и не desktop fallback. Browser Source `ACTOR Voice` расположен поверх video и покрывает canvas 1280x720. Он содержит subtitles, voice и независимый opaque Safe screen. Heartbeat старше 1 секунды, потеря socket, suspend AudioContext или worklet error возвращают Safe и очищают audio. Это визуальный deadman, а не StopStream.

`native.py` связывает dependencies, admission, gRPC bridge, non-headless observer, exact window, model, voice, OBS, actual audio/video probes и guard loop. Production controller работает под отдельным guardian, который сохраняет ownership channel lease, retained process group и OBS/admission transactions. Потеря controller heartbeat приводит к bounded owned cleanup. Lease не освобождается на основании одного signal; требуется доказанное завершение ресурсов. OBS websocket local-only либо подтвержден отдельным PF attestor. Secrets принадлежат отдельным macOS Keychain items; они не включаются в LLM context, argv, Git или public diagnostics.

Якоря: `runtime/actor_broadcast/speech.py:19`, `runtime/actor_broadcast/speech.py:161`, `runtime/actor_broadcast/speech.py:190`; `runtime/actor_broadcast/tts_worker.py:39`, `runtime/actor_broadcast/tts_worker.py:66`; `runtime/broadcast-mlx-requirements.txt:1`; `runtime/actor_broadcast/audio.py:259`, `runtime/actor_broadcast/audio.py:395`; `runtime/actor_broadcast/resources/audio-worklet.js:4`; `runtime/actor_broadcast/resources/renderer.js:17`; `runtime/actor_broadcast/obs.py:246`, `runtime/actor_broadcast/obs.py:426`, `runtime/actor_broadcast/obs.py:687`; `runtime/actor_broadcast/native.py:391`, `runtime/actor_broadcast/native.py:683`, `runtime/actor_broadcast/native.py:911`; `runtime/actor_broadcast/supervisor.py:310`, `runtime/actor_broadcast/supervisor.py:532`; `runtime/actor_broadcast/ownership.py:19`; `runtime/actor_broadcast/secrets.py:16`; `docs/broadcast-renderer-safety.md:1`.

## Twitch chat и lifecycle

Twitch chat использует official Device Flow + Public application + scope `user:read:chat` + EventSub `channel.chat.message`. Это отдельная авторизация от OBS stream authentication. Подключение chat optional: default enabled, явный config opt-out поддержан. В текущих real checkpoint чат был выключен, так как для регистрации Twitch app понадобилась первоначальная 2FA через SMS. Поэтому нельзя обещать фактически проверенный live chat OAuth/EventSub по наличию реализации.

Входящие сообщения bounded, очищаются от control chars, URLs, email/phone и дедуплицируются. Они остаются attributed viewer claims низкого приоритета и не имеют command/control authority. Public in-game discussions работают независимо от Twitch chat.

Lifecycle: Idle -> Preparing -> PreviewReady -> Live -> Ending -> Stopping -> Stopped; unsafe failures -> Failed/Safe cleanup. Preview не запускает публикацию. Public start отдельно проверяет текущую broadcast session, channel, readiness и exact confirmation. Live выставляется только после actual OBS output confirmation. GameOver очищает обычную работу, озвучивает разрешенный результат и farewell, ждет actual local playback и bounded mux drain, затем останавливает owned stream. Disconnect не означает победу. В Ending сохраняется уже проверенная сцена Match, чтобы GameOver оставался виден во время outro.

Якоря: `runtime/actor_broadcast/chat.py:197`, `runtime/actor_broadcast/chat.py:233`, `runtime/actor_broadcast/chat.py:259`; `runtime/actor_broadcast/native.py:488`, `runtime/actor_broadcast/native.py:555`; `runtime/actor_broadcast/coordinator.py:68`, `runtime/actor_broadcast/coordinator.py:160`, `runtime/actor_broadcast/coordinator.py:194`; `docs/broadcast-twitch-login.md:1`; `docs/broadcast.md:294`.

## Что реально измерено, а что еще не принято

Последний явно описанный public checkpoint: `docs/broadcast.md:336`.

- Дата 2026-10-23, public attempt04, source `75b1ed8`, installed Runtime RC19.
- Подтвержденный Live через 43.37 секунды.
- Использован Frankfurt server-only overlay с сохранением OBS authentication.
- Twitch Inspector: Excellent, 720p, около 4 Mbps.
- Четыре обычных Codex participant: 22 unique model turns / 22 decisions, 650962 tokens.
- Итог attempt остается **FAIL**: при общей длительности 137.97 секунды cumulative Warning составил 61.927 секунды и превысил resource limit 60 секунд.
- Owned cleanup и независимый Root postflight passed: все owned resources закрыты.
- Это не completed match и не sustained-load acceptance. Per-player Unity/SDK RSS не сохранен, поэтому нельзя приписать resource pressure конкретному компоненту.
- Recording quality и viewer-audible speech требуют отдельных проверок.

Предыдущие checkpoints документ обозначает историческими, не текущим acceptance:

- 20/20 real Codex public scenarios; first segment p50 1.819 s / p95 5.098 s, text-only, до финального tool-policy hardening.
- Один final-policy dedicated-profile real Codex smoke: first segment 3.357 s; 620 input / 70 output tokens; zero observed tools; no native/audio/Twitch.
- Реальный isolated BF16 short utterance: model load 4.477 s, warmup 1.957 s, first PCM 0.901 s; 1.04 s complete Russian speech за 1.037 s; не joint-load SLO.
- Model allocator peak после load/warmup 5.11 GiB; RSS worker не равен полному Metal/unified-memory footprint.
- Pure tests, fake providers и synthetic render ACK подтверждают wiring/bounds, а не native GPU performance, naturalness, OBS capture или Twitch viewer latency.

Источники: `docs/broadcast.md:334`, `docs/broadcast.md:356`, `docs/broadcast.md:366`, `docs/broadcast.md:368`, `docs/broadcast.md:382`, `docs/broadcast.md:410`; `docs/broadcast-latency.md:1`; `docs/broadcast-motion.md:66`.

## Минимальные формулировки для слайдов

- «Агенты играют. Комментатор наблюдает.»
- «В эфир попадает только разрешенный кадр.»
- «Событие -> контекст -> комментарий -> голос -> Twitch»
- «Текст: gpt-6-luna. Голос: локальный Qwen3-TTS.»
- «История помнит факты. Эфир помнит услышанное.»
- «Публичный эфир проверен. Длительная нагрузка еще исследуется.»

Не использовать без нового доказательства: «стабильный 24/7 эфир», «завершенный автономный live матч», «полностью проверенный Twitch chat», «комментатор видит весь мир», «комментатор знает роли», «Kokoro озвучивает эфир», «локальные unit tests доказывают качество эфира», «viewer latency = first PCM».

## Инвентаризация охвата

Подробно прочитаны principal content paths:

- Все broadcast docs: `docs/broadcast.md`, `broadcast-editorial.md`, `broadcast-motion.md`, `broadcast-renderer-safety.md`, `broadcast-latency.md`, `broadcast-liveliness.md`, `broadcast-twitch-login.md`, config example.
- Protobuf broadcast schema полностью.
- Python `arena.py`, `bridge.py`, `config.py`, `contracts.py`, `context.py`, `director.py`, `editorial.py`, `evidence.py`, `spoken.py`, `segmentation.py`, `admission.py`, `installation.py`, `ownership.py`, `window.py`.
- Основные execution/lifecycle branches `scheduler.py`, `native.py`, `coordinator.py`, `audio.py`, `speech.py`, `tts_worker.py`, `obs.py`, `supervisor.py`, `operator.py`, `chat.py`.
- Provider preparation, model/account policy, output schema, streaming consumption, failure and terminal accounting в `runtime/actor_codex/commentary.py`; authentication API layout `commentary_auth.py`.
- Packaged renderer HTML/JS и AudioWorklet полностью; operator UI wiring и status controls просмотрены по исходникам/документации.
- Unity `BroadcastViewProjector.cs`, `BroadcastSession.cs`, `BroadcastGameAdapter.cs`, `BroadcastPresentationRules.cs`, `BroadcastObserverRules.cs`, `BroadcastDeliveryRules.cs`; bootstrap/bridge в `BroadcastClientController.cs`; render/ACK path и presenter inventory в `BroadcastViewPresenter.cs`; observer branches `MatchSession.cs`.
- Control Plane route, direct-loopback boundary, observer admission manager branches.

Вспомогательные файлы прочитаны по публичному контракту/ключевым веткам/инвентарю символов, без заявления о построчном аудите каждой ветки: `guardian_client.py`, `guardian_services.py`, `guardian_entry.py`, `cli.py`, `latency.py`, `obs_firewall.py`, `obs_firewall_attestor.py`, `secrets.py`, `twitch_login.py`.

Тестовые suites проиндексированы по test names и ключевым проверкам: admission, arena, evidence, commentary, editorial, liveliness, camera/director, native/freshness, speech/audio/revocation, renderer/worklet, OBS, supervisor/guardian, firewall, budget, latency, operator/UI, CLI, auth/Twitch. C# suites: `tests/Actor.Sdk.Tests/Broadcast*.cs`, `tests/Actor.ControlPlane.Tests/BroadcastObserverTests.cs`. Новые тесты не запускались, чтобы соблюдать режим чтения исходного репозитория.

Объем Python broadcast subsystem с browser resources и commentator/auth — около 11289 строк по wc; это масштаб изученной подсистемы, не метрика качества. Generated bindings не анализировались построчно: первичен protobuf contract.
