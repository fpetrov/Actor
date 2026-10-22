# M8: начальный индекс трёх попыток Codex

Это снимок **attempt-01–03**, а не итоговая приёмка серии или релиза. [Безопасный индекс](initial-three.json) повторно проверен неизменённым строгим M7 evaluator по исходным отчётам, замороженным клиентским доказательствам и метаданным изоляции/cleanup. Скопированный `passed:true` не используется как доказательство. [Реестр](initial-three-registry.json) сохраняет обе неудачи подготовки.

Последующее подтверждённое продолжение: [реестр01–08](attempts-01-08-registry.json), [индекс01–08](attempts-01-08.json), [условия](attempts-01-08-conditions.json). Исторические снимки ниже не перезаписываются.

| Попытка | Результат | Независимый Codex-старт / GameOver | Фактическая игра |
|---|---|---|---|
| 01 | Строгая приёмка GameOver: PASS | 4 участника / да | 141,554 с; Impostor, `parity`; 2 kill, 1 sabotage, 0 body report, 0 meeting, 0 ballot, 0 Crew task |
| 02 | Ошибка подготовки; не PASS | Не подтверждён / нет | По наблюдению оператора: занятый bridge port; отчётов Runtime нет |
| 03 | Ошибка подготовки; не PASS | Не подтверждён / нет | По наблюдению оператора: staging не пуст; отчётов Runtime нет |

Знаменатель этого снимка: **3 попытки, 1 подтверждённый старт матча, 1 принятый GameOver, 2 ошибки подготовки**. Причины setup в таблице — классификация оператора; helper самостоятельно подтверждает отсутствие достаточных launch/Runtime/terminal доказательств и не публикует сырые ошибки. На момент снимка уже существовали ещё две директории последующих попыток: `inventory.complete=false`, поэтому этот индекс не заявляет полноту всей продолжающейся серии или пять успешных стартов.

## Расход и измерения

Все следующие цифры получены только из четырёх фактических Runtime-отчётов попытки 01. Из 12 ожидаемых участников зарегистрированных попыток **8 не имеют Runtime-отчёта**. Их расход, число model turns и стоимость неизвестны; они не считаются нулевыми.

| Участник | Turns | Input / cached input | Output | Всего токенов | Среднее ожидание решения / p95, с | Средний / максимальный context, байт |
|---|---:|---:|---:|---:|---:|---:|
| agent-01 | 14 | 386 633 / 286 720 | 2 297 | 388 930 | 6,036 / 19,310 | 14 223 / 19 673 |
| agent-02 | 8 | 200 715 / 123 264 | 2 043 | 202 758 | 10,138 / 21,244 | 12 873 / 18 018 |
| agent-03 | 7 | 145 750 / 97 792 | 1 583 | 147 333 | 8,784 / 16,762 | 12 698 / 23 028 |
| agent-04 | 8 | 209 888 / 136 704 | 2 277 | 212 165 | 11,118 / 17,048 | 14 301 / 18 641 |

Известная сумма: **37 turns, input 942 986, cached input 644 480, output 8 200, всего 951 186 токенов**. Cached input входит в input. Прямых вызовов инструментов модели: 0. Официальный SDK 0.162.1, `read-only`, ephemeral; уникальные процессы, порты, actor/session bindings, профили и базы памяти прошли проверки. Provider не сообщил `cost_usd`: стоимость `null`, пересчёта токенов в доллары нет. Ожидание решения включает вызов модели; это не игровой RTT или FPS. Эти распределения малы и относятся к одному матчу.

## Границы выводов

Полный социальный цикл **не подтверждён**: в authoritative replay нет body report, Discussion/Voting или ballot. Полученные сообщения (3/5/5/5 у участников) сами по себе не доказывают Discussion-канал или двух model-отправителей. В ранее закрытых эпизодах остались 26 результатов с `[private_activity]`; helper явно отмечает это ограничение и не восстанавливает действия по косвенным счётчикам. Новая Runtime-инструментация не применяется задним числом к этому снимку.

После строгого GameOver разрешён отдельный обзор Crew: agent-02 — 5 succeeded / 2 cancelled / 0 failed Skill, agent-03 — 5 / 1 / 0, agent-04 — 4 / 3 / 0. Причины завершения эпизодов: `episode_superseded`, `participation_terminal`. Один публичный результат движения agent-03 подтверждает `stopped=true`, `room_change_confirmed=true`, 5 достигнутых waypoint и 0 reroute. Остальные закрытые идентификаторы и приватные outputs не позволяют установить выбор task или точную причину отмены. Ноль завершённых Crew tasks остаётся фактом результата; доказанного дефекта навигации/механики из этих записей не следует.

Оценка обмана Impostor: **`unassessed`**. Победа, kill и sabotage не заменяют ручного анализа фактических разрешённых заявлений и поведения. Human mixed/control, native UI/аудио/640 ms эффекты, приватность, производительность, fault-проверки и deployment/CI требуют отдельных доказательств.

## Последующая попытка 04: сохранённая неудача

[Отдельная безопасная диагностика](attempt-04-diagnosis.json) не меняет исторический знаменатель initial-three. Supervisor завершился с exit 1, `runtime_match_failed`. agent-01: `failed/brain_failure`, 197,114 с, 14 решений; остальные — `cancelled/participation_cancelled`, около 197,36 с и 13/13/11 решений. Известный скалярный `measured_tokens` четырёх процессов: 1 998 468. Детальные turns/SDK usage/стоимость неизвестны: публичные episodes пусты. Это не принятое GameOver и не доказательство конкретной ошибки навигации или pursuit; нижележащего provider failure code в разрешённых DTO нет. Новый экземпляр использовал комнату предыдущей setup-неудачи, в которой участники ранее не были допущены; успех или повторный матч из этого не выводится.

## Продолжающийся реестр 01–07

[Полный входной реестр](attempts-01-07-registry.json) и [безопасный снимок](attempts-01-07.json) сохраняют семь завершённых попыток: 1 accepted, 6 failed (две setup и четыре Runtime). Исходный initial-three остаётся неизменным. Строгое доказательство official SDK start пока есть только для 01: в неуспешных закрытых отчётах скрыты provider turns. Несколько реальных запусков нельзя автоматически превратить в пять проверенных SDK-стартов по имени Brain или суммарным токенам.

[Попытка 05](attempt-05-diagnosis.json): agent-04 завершился `failed/brain_failure`, 174,207 с, 14 решений, `measured_tokens=537621`; остальные отменены, общий известный скаляр 1 624 127. Превышение 500 000 согласуется с гипотезой лимита, но не доказывает `codex_usage_limit`: скаляр суммирует эпизоды, лимит применяется к каждому новому Brain, а подробности отсутствуют.

06: agent-03 — `failed/brain_failure`,238,328 с,14 решений,577059 measured tokens. 07: agent-01 — `failed/brain_failure`,181,312 с,15 решений,566833 measured tokens. Остальные участники отменены. Все06/07 `authoritative_terminal=false`, provider telemetry отсутствует, финальный GameOver не принимается.

[Условия эксперимента](attempts-01-07-conditions.json) фиксируют только фактические поля `cognitive_config`. На этом снимке отчёты 01/04/05/06/07 показывают Brain limit 500 000, warning 100 000, Participation limit 3 000 000. План повысить Brain limit до 1 500 000 и warning до 800 000 не считается применённым без подтверждения последующих DTO. При повышении лимита или смене интеграционной сборки начинается новое условие эксперимента; объединённый процент побед не будет сопоставимым benchmark. В сборках 01–07 обнаружен пропуск четырёх новых M8 SDK registrations; эти попытки не доказывают доступность новых многоэтапных task-механик.

Обновление после завершения:

```sh
python3 spikes/release-validation/series.py \
  --registry docs/release-validation/models/attempts-01-07-registry.json \
  --root build/milestone8 \
  --output docs/release-validation/models/attempts-01-07.json
```

## Попытка 08: полный фактический социальный цикл

После исправленного полного SDK binding и повышения **подтверждённого DTO** Brain budget до1 500 000/warning800 000 (Participation3 000 000) принята08: **286,153 с, Crew / impostors_eliminated**,1 kill,1 body report,1 meeting,3 ballots,6 Crew tasks,0 sabotage. Это новое условие эксперимента, не сравнимый процент побед со сборками01–07.

Независимый sanitizer повторно прошёл все8 checks полного цикла. Report Skill у точного автора body report отменён phase transition, но его выбор и accepted server replay совпадают. Три Codex (agent-01/02/04) выбрали и успешно завершили4/3/4 typed Discuss; actual server/client receipt counters и42 received messages подтверждают отправку/получение. Присутствуют Discussion/Voting,3 actor-bound ballots и полный postgame replay. Masked Skill results0; сообщения и приватные аргументы не публикуются.

| Участник | Turns | Input / cached input | Output | Всего токенов | Получено сообщений |
|---|---:|---:|---:|---:|---:|
| agent-01 | 18 | 582 213 / 417 024 | 4 843 | 587 056 | 14 |
| agent-02 | 16 | 426 796 / 282 368 | 5 416 | 432 212 | 12 |
| agent-03 | 7 | 198 738 / 121 984 | 952 | 199 690 | 1 |
| agent-04 | 18 | 565 554 / 407 552 | 4 031 | 569 585 | 15 |

Известная сумма08:59 official turns,1 788 543 total tokens,0 direct model tools; цена неизвестна. Headless loop около60 Hz не является rendered FPS.

[Ограниченный ручной postgame review](attempt-08-disguise-review.json): точный Codex Impostor выбрал FakeTask2 раза, один результат succeeded. Source `episode-2-result-4`, typed Skill1.0.0, требует matching accepted+completed own WorkTask server receipt. Это **наблюдаемая попытка поведенческой маскировки через работу**, не доказательство убедительной лжи, false alibi/accusation, изменения чужих убеждений или причинного влияния на исход. Отдельной fake-work записи в server postgame replay нет; contract basis и это ограничение указаны явно. Helper grade `observed` относится только к этому узкому поведению.

Итог данного снимка:8 попыток,2 accepted GameOver,6 failures,1 полный социальный цикл. Строго проверены2 independent official SDK match starts; provider metadata прежних неуспешных попыток не восстанавливается задним числом. Gate пяти таких стартов остаётся false. Никакого общего release PASS этот индекс не выдаёт.

## Воспроизведение

Из корня репозитория, при наличии исходных локальных артефактов:

```sh
python3 spikes/release-validation/series.py \
  --registry docs/release-validation/models/initial-three-registry.json \
  --root build/milestone8 \
  --output build/milestone8/series-initial-three-reproduced.json
python3 -m unittest discover -s spikes/release-validation -p 'test_series.py'
```

Exit 1 ожидаем: snapshot сохраняется, но общие gates серии не выполнены. Число последующих незарегистрированных директорий при повторе может увеличиться. Исторический JSON здесь фиксирован. 26 offline-тестов helper проходят; это тесты строгого санитайзера на синтетических данных, а не дополнительные живые матчи. Новый bounded provider telemetry fallback проверен против production scalar projector; он не дописывает прежние01–07 и не ослабляет GameOver. Публичные JSON содержат только разрешённые агрегаты и metadata; промпты, рассуждения, сообщения, credentials, actor/session IDs, PID и частные файловые деревья не копируются.


## Продолжение09–12: финальный full-binding cohort

[Реестр01–12](attempts-01-12-registry.json) и [строгий индекс01–12](attempts-01-12.json) сохраняют все12 попыток:6 принятых GameOver и6 прежних неудач (2 setup,4 Runtime). Исторические JSON не перезаписываются. [Cohort08–12](full-binding-08-12.json) отдельно подтверждает пять полных матчей: во всех20 точных accepted sessions присутствуют новые WaitingPlayers, TaskWork, TaskStep и KillPresentation capabilities. Снимок08 использует Runtime534;09–12 — Runtime545 с новым public SDK stream collector.

| Попытка | Authoritative outcome | Длительность | Kills / tasks / body reports / meetings / votes | Typed Discussion senders | Полный цикл |
|---|---|---:|---|---:|---|
|09|Crew, impostors_eliminated|288,414 с|1 /2 /0 /1 /3|3|Нет: body report отсутствует|
|10|Impostor, parity|137,881 с|2 /4 /0 /0 /0|0|Нет|
|11|Crew, tasks_completed|169,599 с|0 /8 /0 /0 /0|0|Нет: валидная победа по задачам|
|12|Crew, impostors_eliminated|274,770 с|1 /6 /1 /1 /3|3|Да|

Каждая строка повторно проверена неизменённым строгим evaluator по четырём официальным независимым CodexBrain, собственным terminal client proofs и одинаковому server-authoritative summary. Full cycle12 дополнительно соединяет actual model-selected Skills с kill, body-report и ballot replay; succeeded typed Discuss требует настоящего server receipt и подтверждается counters отправителей/получателей. Это не доказательство того, что конкретный текст был прочитан чужим Brain, и не вывод о внутренней мотивации модели.

[Фактическая проверка collector](attempts-09-12-stream.json):195 SDK turns,28 interrupted decisions; все28 прерванных и все остальные решения имеют наблюдённые sdk_item_lifecycle counters, всего0 model tools. Ноль не выведен из конфигурации. Первичная публичная subscription собирает started/completed items до actual terminal; неизвестный item, неполный stream или отмена до terminal оставляют coverage неизвестным. Старые отсутствующие counters не восстановлены задним числом.

[Условия](attempts-09-12-conditions.json): четыре plain Codex peers с одной Objective, свежими bridge ports, SQLite/profile/process groups. Публичный профиль и budgets неизменны. Во время серии root параллельно выполнял локальные Unity QA builds; точное пересечение и CPU contention не измерялись. Поэтому результаты не являются сравнением FPS, latency или win rate. Известный расход09–12:input5 203 251 (из них cached3 608 960), output38 872, всего5 242 123 токена. Cached input входит в input; стоимость неизвестна, долларовой оценки нет.

[Очистка](attempts-09-12-cleanup.json): все принадлежащие четырём запускам группы завершены, staging API показывает0 rooms, дополнительных13 AI-v-AI запусков нет. UDP socket этот helper отдельно не измерял. Ручной review08 сохранён только в прежнем узком смысле attempted behavioral disguise; он не доказывает убедительную ложь или изменение чужих убеждений. Общий helper gate не является полным release/native/TLS acceptance.
