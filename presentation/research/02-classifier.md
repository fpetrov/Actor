# ACTOR: классификатор и когнитивный контур

Исследовательский отчет от 23 октября 2026 года. Репозиторий читался без изменений, без fetch/pull, запуска моделей, игры или тестов. Изученный локальный commit: `aa5d46ec1fee4fbcab9292fc1f58f48fee3e1bb8`; на момент чтения `origin/main` указывал на тот же commit. Само по себе локальное remote-tracking имя не подтверждает актуальное состояние GitHub; удаленную версию проверяет основной агент.

## Главная идея

Стратегическое мышление отделено от исполнения. Brain выбирает типизированный Skill либо ограниченный исполнимый план. Scheduler исполняет Skills без постоянного обращения к LLM. Быстрый классификатор оценивает новые события в контексте реально принятого плана и рекомендует, требуется ли стратегический пересмотр. Runtime сохраняет полномочия отменять действия, менять когнитивную ревизию, сохранять память и вызывать Brain.

```text
Авторитетный игровой сервер
  → разрешенное персонажу наблюдение/событие в Unity
  → gRPC → RuntimeSession
  → EventRouter: проверка, дедупликация, ограниченные очереди
  → немедленное критическое правило / выбранный классификатор
  → политика Router
      ├─ продолжить Skill / принятый план
      ├─ выполнить ранее принятую условную реакцию
      └─ остановить Skill / отбросить устаревший ответ
          → актуальный контекст + память → Brain
  → типизированный Skill / план
  → Scheduler → gRPC → Unity → сервер
```

Источник общей модели: [docs/event-routing.md:3](/Users/fpetrov/PycharmProjects/Actor/docs/event-routing.md:3), [runtime/actor_runtime/events.py:304](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/events.py:304), [runtime/actor_runtime/objectives.py:289](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/objectives.py:289).

## Границы и порядок обработки

- Не каждый игровой кадр классифицируется. Transform/velocity обновляют WorldState. В Router поступают типизированные события и, в новой реализации планов, первое свежее собственное наблюдение, соответствующее активному условию. Старый `docs/event-routing.md:29` формулирует правило без исключения для планов; для текущего поведения приоритет имеют код и `docs/executable-plans.md:25`.
- Классификатор получает собственную цель, текущий Skill, компактное собственное состояние и снимок фактически принятого плана. Будущие действия не восстанавливаются из последующих результатов.
- Доверенные критические правила оцениваются немедленно, даже если асинхронный классификатор занят. Они могут инвалидировать ответ Brain до завершения медленного вычисления. Mandatory review невозможно подавить нейросетью.
- Обычная оценка содержит relevance, urgency, buffer, remember, interrupt, review и confidence. Нейросеть дает рекомендацию; команды она не отправляет.
- Router различает «получено», «оценено», «запомнено», «включено в контекст» и «обработано». Только принятый ответ той же когнитивной ревизии подтверждает обработку.
- Очереди и дедупликация ограничены. Обычные события можно агрегировать и явно вытеснять; критическая часть защищена. Переполнение защищенной части приводит к явному безопасному отказу Objective.
- При пересмотре Runtime сохраняет Objective, отзывает право текущего действия отправлять команды, дожидается cleanup и только затем принимает новое действие. Проверка ревизии повторяется перед принятием ответа и перед dispatch.
- Запрос review не равен фактическому вызову LLM: события могут объединиться, агент может умереть, матч может закончиться или уже существовать естественная точка следующего решения. Качество классификации и число вызовов Brain нужно измерять отдельно.

Источники: [session.py:119](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/session.py:119), [events.py:130](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/events.py:130), [events.py:163](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/events.py:163), [events.py:259](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/events.py:259), [events.py:304](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/events.py:304), [events.py:426](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/events.py:426), [objectives.py:344](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/objectives.py:344), [INTEGRATION_AND_DATA.md:34](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/INTEGRATION_AND_DATA.md:34).

## MiniLM v7

Это не маленькая генеративная LLM. Это дообученный BERT-энкодер с бинарной классификационной головой: 3 слоя, hidden size 384, около 17,4 млн параметров. База — `sentence-transformers/paraphrase-MiniLM-L3-v2`, revision `4ca70771034acceecb2e72475f72050fcdde4ddc`. Выход отвечает на один вопрос: продолжать принятый план или запросить новый стратегический ответ Brain. Модель не генерирует план, не выбирает команду, не определяет скрытую роль другого игрока.

| Параметр | Текущая поставка |
|---|---|
| Версия | `minilm-v7-r2` |
| Статус | `development_only` |
| Архитектура | `BertForSequenceClassification`, 3 слоя, 384 hidden, 12 attention heads |
| Параметры | Около 17,4 млн |
| Веса | 69 568 832 байта, FP32 |
| Input | `ClassifierInput` 0.1 / 0.2 |
| Renderer | `features.render_canonical_v1` |
| Предел | 512 токенов; без молчаливого усечения |
| Решение | `softmax(logits)[1] >= 0.5` |
| Calibration | Score не калиброван как вероятность |
| Inference | Локальный CPU, 2 потока, `local_files_only=True` |
| Runtime Core | Не импортирует PyTorch |
| Default classifier | `rules`; MiniLM включается явно |
| SHA-256 весов | `fd10238301587a517815ea573d5923731d768c4ded9e99f972e4b941a77f09ff` |

Структурированный вход содержит игру/ruleset, собственную известную роль, фазу, цель, текущий план, событие, источник факта и ограниченные наблюдения с давностью. Имена акторов канонизируются, чтобы конкретные идентификаторы не становились стратегическим признаком. Структурные связи идентичности сохраняются. Свободный текст, room/task IDs, источник и возраст не заменяются произвольно.

Runtime projection использует только уже разрешенные собственные наблюдения. Postgame player roles, winner, replay и будущие результаты не подмешиваются во вход. Своя private роль допустима: агенту она известна. Реплика обсуждения передается отдельно с `from_self`, а не всей историей чата. Неизвестный полный план остается неизвестным: одиночный Skill не превращается в выдуманную последовательность.

Поведение провайдера:

1. Критические правила сохраняют прямой маршрут.
2. Перед оценкой фиксируется снимок входа.
3. Строится projection и canonical text; модель дает binary review и score.
4. Для neural review провайдер рекомендует important, remember, interrupt и review; для continue оставляет routine/buffer.
5. Unsupported input, переполнение 512 токенов, timeout и ошибки приводят к правилам с отдельной диагностикой.
6. В каждый момент есть не более одного незавершенного PyTorch-вычисления на экземпляр модели. После timeout новые запросы не образуют очередь за зависшим worker.

Cold start выполняется через async factory в worker thread. Это важно для heartbeat: первоначальная синхронная загрузка четырех моделей блокировала loop примерно на 8 секунд и приводила к разрыву соединений. Исправление вынесло загрузку модели в поток; capture создается только после успешной загрузки, в event loop. Отмена инициализации не создает capture. Сам background thread после отмены может закончить загрузку позже — ограничение `asyncio.to_thread`.

Источники: [README.md:14](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/README.md:14), [model.py:15](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/model.py:15), [provider.py:23](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/provider.py:23), [provider.py:81](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/provider.py:81), [features.py:37](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/features.py:37), [projection.py:22](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/projection.py:22), [_legacy_projection.py:112](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/_legacy_projection.py:112), [report.json](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/checkpoints/v7/report.json), [model/config.json](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/checkpoints/v7/model/config.json).

## Исполнимые планы

Добавлены в `c8a29ef` вместе с интеграцией MiniLM:

- 1–4 последовательных SkillCall;
- до двух одноразовых условий;
- необязательный однократный fallback после ошибки;
- условие только `nonempty` для объявленного repeated/string поля согласованной observation/event capability;
- никаких произвольных программ или исполнения естественного языка как условия;
- все вызовы валидируются перед принятием и повторно Scheduler;
- последовательные шаги и заранее принятая реакция исполняются без дополнительного Brain turn;
- окончание последовательности, реакции или fallback возвращает управление Brain;
- критический lifecycle и запрос neural review отменяют устаревший план.

Свежесть условия — не более двух секунд. Реакция заменяет остаток старой последовательности; это не полноценный язык ветвления, циклов или произвольных retry. Планы не сохраняются между Objective episodes. Уже истинное условие проверяется при принятии плана по свежим собственным observations.

`aa5d46e` исправляет область действия наблюдений: они привязаны к ID принятого плана, не переносятся на следующий план и разрешают типы через negotiated extension registry. Чужие observations не могут активировать условия. Переоценка того же observation под новым планом имеет новую plan-scoped identity.

Источники: [brains.py:28](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/brains.py:28), [plans.py:13](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/plans.py:13), [plans.py:48](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/plans.py:48), [plans.py:84](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/plans.py:84), [objectives.py:297](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/objectives.py:297), [docs/executable-plans.md:9](/Users/fpetrov/PycharmProjects/Actor/docs/executable-plans.md:9).

### Наглядная демонстрация

Агент выполняет задание и замечает тело:

- В принятом плане уже есть условие «если видно тело — сообщить»: MiniLM допускает продолжение принятого плана, Runtime останавливает текущее действие и выполняет ранее выбранную реакцию без нового Brain-вызова.
- Условия нет: на том же событии MiniLM запрашивает новый стратегический ответ.

Этот контраст проверен настоящими весами v7 в локальном typed integration fixture. До отправки report Brain вызывается один раз. Это хорошая объясняющая анимация, но не независимый новый полноценный матч. [test_minilm_integration.py:21](/Users/fpetrov/PycharmProjects/Actor/runtime/tests/test_minilm_integration.py:21).

## Jev: альтернативный внешний классификатор

Jev и MiniLM — разные провайдеры, их нельзя объединять в одну модель или приписывать им одни и те же метрики.

- Jev использует TypeSafe, SDK `typesafe-sdk==0.7.5`, default model `jev-1.13.0`.
- Один `system_one` запрос объединяет Choice(relevance), Score(urgency), Noul(remember), Noul(interrupt), Noul(review).
- Передается ограниченная цель, идентификатор текущего Skill, policy tags и одно событие; не вся память и не весь WorldState.
- Credentials-подобные поля редактируются. Default budget — 8 KiB.
- Включается явно; missing key, timeout, 429, ошибка и лимиты приводят к правилам.
- Критические или детерминированно достоверные правила обходят внешний запрос.
- Default deadline 1,5 секунды, concurrency 1, 30 requests/minute, один ограниченный retry для 429/5xx.
- Confidence gate .85 и Noul uncertainty band .35… .65 — рабочая политика, а не доказательство calibration. Неопределенность вызывает ограниченный review.
- В опубликованной validation Jev API key отсутствовал: живые accuracy/latency/cost не измерялись. Контракт SDK проверялся offline transport.

Источники: [classifier.py:55](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_jev/classifier.py:55), [classifier.py:102](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_jev/classifier.py:102), [config.py:24](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/config.py:24), [docs/classifiers.md:13](/Users/fpetrov/PycharmProjects/Actor/docs/classifiers.md:13).

## Brain / Codex: текущее поведение

Важное изменение относительно исторических milestone-отчетов: сейчас один SDK-процесс обслуживает Objective, но каждый decision получает свежий ephemeral thread. Старую модельную переписку не копируют; непрерывность обеспечивают актуальный typed context, предыдущий SkillResult и WorkingKnowledge. Это ограничивает накопление истории. Статистика токенов и лимиты сохраняются на уровне эпизода.

Objective входит как доверенный `TextInput`, Skill schemas — как установленный trusted catalog, игровые observations/events/results/memory — как `ExternalMessage`. Произвольный текст игрока не получает авторитет инструкции пользователя. Output — ограниченный JSON, типизированный как `SkillCall | SkillPlan | FinishObjective`; поля каждого Skill проверяются по его Pydantic schema. Одно исправление невалидного ответа допускается, повторная ошибка завершает Objective контролируемым отказом.

Используется официальный `openai-codex==0.162.1`, публичные SDK methods. Временная пустая рабочая папка, read-only sandbox, deny approvals, выключенные shell/web/apps/MCP. Игровые действия выполняют Runtime Skills, не прямые Codex tools. Soft interrupt инвалидирует локальный epoch и дожидается остановки старого turn. Недренируемый turn завершает Objective, а не запускает конкурирующий ответ.

На уровне матча MatchEpisodeController координирует ограниченные Objective episodes. Между эпизодами закрывается старый Brain; RuntimeSession, Scheduler, Router и scoped memory сохраняются. FinishObjective завершает эпизод; только авторитетный GameOver завершает матч. Смерть приостанавливает новые модельные вызовы. Default participation limits: 1800 секунд, 32 episodes, 160 decisions, 1,5 млн measured tokens.

Источники: [docs/codex.md:62](/Users/fpetrov/PycharmProjects/Actor/docs/codex.md:62), [docs/codex.md:93](/Users/fpetrov/PycharmProjects/Actor/docs/codex.md:93), [docs/codex.md:128](/Users/fpetrov/PycharmProjects/Actor/docs/codex.md:128), [brain.py:47](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_codex/brain.py:47), [brains.py:52](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/brains.py:52), [episodes.py:24](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/episodes.py:24), [config.py:70](/Users/fpetrov/PycharmProjects/Actor/runtime/actor_runtime/config.py:70).

## Данные, обучение и наблюдаемость

MiniLM v7 — продолжение v6: 2045 уникальных train inputs, 290 dev inputs, две эпохи AdamW `5e-6`, batch 8, seed `20261023`, class-balanced weighted loss. Обновлены encoder и classification head. Исторические приватные datasets и предыдущие веса не опубликованы: точное воспроизведение исторического обучения из поставки невозможно.

Trainer отвергает пересечение match groups, одинаковых canonical rendered inputs и конфликтующие метки. Все агенты, события, повторные сообщения и варианты одной ситуации/матча должны оставаться в одной группе. Проверка не обнаруживает автоматически все смысловые дубли. Candidate сохраняется в новой папке и не продвигается автоматически. Во время игры веса не меняются.

Из training data исключены неоднозначные завершения Skill: успешное завершение и так вызывает обычный следующий ход Brain, а бинарный input не содержит pending-decision state. Поэтому нельзя объявлять каждое завершение потребностью в дополнительном review.

Необязательный capture связывает фактический input → score/provider output → post-policy Router → принятый ответ Brain → шаги плана/Skills → команды и результаты. Идентификаторы используются реальные: call_id/event_id/objective_id/plan_id/command_id. Capture пишет `label: null` и `is_ground_truth: false`: ответ модели не превращается в правильную метку.

По умолчанию capture выключен. При включении root directory 0700, файлы 0600, 256 KiB на строку и 64 MiB на файл. Только own-agent permitted state. Сбой записи диагностируется, но не меняет игровое решение; закрытие восстанавливает прежний trace sink. Для будущего обучения нужны человеческое ревью и независимые целые holdout matches.

Источники: [training.py:13](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/training.py:13), [training.py:77](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/training.py:77), [capture.py:56](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/capture.py:56), [capture.py:116](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/capture.py:116), [INTEGRATION_AND_DATA.md:66](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/INTEGRATION_AND_DATA.md:66), [training-data-manifest.json](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/evidence/training-data-manifest.json).

## Проверенные показатели и границы интерпретации

Все числа ниже взяты из поставленных отчетов; новый benchmark/матч агент-исследователь не запускал.

| Что измерено | Результат | Ограничение |
|---|---|---|
| Development validation v7 | 290 входов: TP 67, TN 220, FP 2, FN 1; recall 98,53%, macro F1 98,57% | Набор участвовал в выборе кандидата; не независимая финальная оценка |
| Замороженный replay | 486/486 inputs и predictions совпали; 245/245 protected critical-rule решений сохранены | Проверка воспроизводимости/policy parity |
| Ordinary holdout cohort | 182 случая; model нашла единственный нужный review после убийства, rules пропустили; extra reviews 0 | Положительный пример один; новые human-reviewed labels — 0 |
| Реальные fixture matches | 4 независимых матча, 2 сценария × rules/MiniLM, все прошли | Детерминированные Brains, 0 external LLM calls; не качество стратегии и не win rate |
| Meeting scenario | 1 kill, 1 report, 1 meeting, 3 votes, impostor ejected | По одному финальному успешному прогону на провайдер |
| Task scenario | 8 tasks completed | По одному финальному успешному прогону на провайдер |
| MiniLM в двух реальных матчах | 85 neural assessments: p50 16,98 мс, p95 47,25 мс, max 51,24 мс | Apple M5, четыре параллельных агента; projection/thread scheduling включены |
| Single-provider synthetic benchmark | p50 2,89 мс logging off / 2,91 мс logging on | Короткий синтетический input, 300 measured + 20 warmup; не SLA |
| Rules Runtime overhead | p50 0,040 → 0,042 мс | Один typed action-result probe; noise и условия hardware |
| Publication test receipt | 1877 passed, 120 subtests passed, 8 skipped | Исторический локальный receipt изменения; не новый прогон исследователя |

Источники: [report.json](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/src/actor_minilm/checkpoints/v7/report.json), [rules-comparison.json](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/evidence/rules-comparison.json), [live-acceptance.json](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/evidence/live-acceptance.json), [performance.json](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/evidence/performance.json), [publication-validation.json](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/evidence/publication-validation.json).

У holdout replay были три замороженные игровые попытки; две завершились, третья закончилась ошибкой Codex SDK. MiniLM управляла одной из завершившихся игр; две другие использовали Qwen. Эти входы не участвовали в train v7. Это отдельная когорта от четырех fixture acceptance matches. В raw all-events диагностике есть terminal lifecycle reviews; их нельзя напрямую считать дополнительными LLM calls.

Документация отдельно сохраняет прежние проблемы: собрание без зарегистрированных голосов и прерывание Brain новыми репликами. Эта поставка не объявляет их исправленными. Приоритеты обсуждения и deadline голосования требуют отдельной работы Runtime. [INTEGRATION_AND_DATA.md:81](/Users/fpetrov/PycharmProjects/Actor/classifiers/minilm/INTEGRATION_AND_DATA.md:81).

Нельзя заявлять: «модель снижает tokens на X%», «повышает win rate», «точность 100%», «в целом лучше rules», «все обычные события обрабатываются за 3 мс», «Jev протестирован вживую». Реализация создает условия для редких стратегических вызовов, но величина экономии и стратегическое качество в независимых LLM matches не установлены.

## Формулировки для лаконичных слайдов

- Большая модель строит план. Быстрый контур решает, когда его пересмотреть.
- Мышление — по событию. Исполнение — непрерывно.
- Агент видит только то, что доступно его персонажу.
- Один протокол. Сменяемые Brains. Проверяемые Skills.
- Новый факт либо укладывается в план, либо запускает пересмотр.
- MiniLM работает локально. Критические правила сохраняют приоритет.
- Каждое действие связано с наблюдением, решением и результатом.

## Инвентарь покрытия

Полностью прочитаны небольшие human-authored файлы пакета `classifiers/minilm`: README, INTEGRATION_AND_DATA, THIRD_PARTY_NOTICES, pyproject, requirements, `__init__`, contract, features, model, provider, projection, legacy/v5 projection, capture, evaluation, training, CLI; обе adapter policy text resources; benchmarks/benchmark.py и runtime_ingress.py; все test_classifier.py; JSON examples, report/manifest/config; все evidence JSON и code-review.md. Проверялись имена/назначение model/tokenizer файлов; binary weights, tokenizer vocabulary и tokenizer.json намеренно не читались как исходный текст. Лицензия базы идентифицирована как Apache 2.0 через notices и наличие LICENSE-BASE-MODEL; она не распространяет автоматически лицензию на весь Actor.

Полностью прочитаны `runtime/actor_runtime/classifiers.py`, `events.py`, `plans.py`, `brains.py`, cognitive config, `runtime/actor_jev/classifier.py`, `runtime/actor_codex/brain.py`, `runtime/tests/test_minilm_integration.py`; ключевые lifecycle/dispatch/revision части objectives.py, episodes.py, adapter.py и session.py. Это не утверждение о построчном чтении всех файлов Runtime. По остальным tests прочитаны names/coverage surface и релевантные fixtures/assertions; полный запуск отсутствовал.

Документы: полностью прочитаны docs/classifiers.md, docs/event-routing.md, docs/executable-plans.md и docs/brains.md; текущий lifecycle/security/history части docs/codex.md; исторические acceptance docs/cognitive-validation/README.md и docs/event-validation/README.md. Старые milestone детали использовались как история, а не как описание текущего thread lifecycle.

Git: read-only status/remotes/log; commit aa5d46e просмотрен полностью по затронутым механизмам; c8a29ef и последние Codex/history commits идентифицированы по истории и соответствующему актуальному коду/docs. Рабочее дерево на момент read-only проверки не имело показанных изменений. GitHub network read не выполнялся этим подагентом.
