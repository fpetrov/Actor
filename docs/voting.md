# Meetings и голосование

Правила SocialDeduction исполняет Dedicated Server через ту же pure
`SocialMatchAuthority`, которую используют Human и ACTOR clients.

## Созыв

Живой участник в Exploration может сообщить об unreported теле, которое
действительно видит и до которого не более configured body-report distance
(по умолчанию 1.2). Emergency meeting требует физической доступности настоящего
meeting terminal, личного cooldown и оставшегося лимита.

По умолчанию допускаются три meeting за матч и один emergency call на игрока;
личный emergency cooldown — 90 секунд. Body reports используют общий лимит,
но не расходуют личный emergency call. Неуспешный запрос не расходует лимит.
Сервер проверяет match, phase epoch, короткий expiry и correlation каждого
запроса. Immutable configuration задаёт интервалы и лимиты в безопасных bounds.

## Discussion → Voting → VoteResolution

Discussion по умолчанию длится 90 секунд, Voting — 60 секунд. Только серверные
дедлайны переводят фазу. Короткая VoteResolution длится одну секунду перед
возвращением в Exploration, если матч ещё не завершён.

Персонажи остаются в текущих мировых координатах. Сервер останавливает movement,
убийства и физическую работу; незавершённые task/repair leases отменяются.
Meeting roll-call явно раскрывает identities/names и Life участников, сохраняя
скрытые Duty, Alignment и удалённые позиции. Forfeit во время meeting обновляет
roll-call сразу. В Exploration отдельного общего death roster нет.

Живые участники используют существующую typed Standard Communication через
канал `discussion`. Outside meetings доступны разрешённые `direct` и
`proximity`, с серверной видимостью получателей. Свободный `station` broadcast
в SocialDeduction закрыт. Dead/Ejected/Disconnected/Spectator не могут писать
живым или голосовать.

Таймер активного sabotage приостановлен на Discussion, Voting и VoteResolution.
После возвращения в Exploration сервер восстанавливает оставшееся время.
Общий match timer продолжает идти.

## Окончательный ballot

У каждого живого участника один окончательный ballot на meeting: допустимый
живой кандидат либо явный Skip. Self-vote разрешён. Пропущенный дедлайн означает
abstention и не создаёт автоматический Skip. Замена уже принятого ballot
отклоняется; повтор того же correlation воспроизводит сохранённый результат.
Сервер определяет voter identity по owned connection.

До resolution игрок получает только собственный ballot; общего промежуточного
tally нет. Голос выбывшего через disconnect/control loss аннулируется.
Кандидат должен оставаться живым для ejection. Поданные ранее голоса за
выбывшего кандидата могут отображаться в итоговом историческом tally, но не
делают его допустимым лидером.

При Voting deadline сервер выбирает единственного лидера среди допустимых
кандидатов. Ejection происходит, только если его результат строго больше Skip.
Равенство кандидатов, равенство с Skip или отсутствие голосов приводит к
`no_ejection`. Кворума нет: один действительный голос может решить исход при
abstention остальных.

Исключённый участник получает `Ejected`; active match не раскрывает его
Alignment. Проверка победы выполняется сразу после resolution. GameOver
разрешает итоговое раскрытие ролей через bounded server summary.

## Epoch fencing и завершение

Каждая фаза имеет новую phase revision. Старый kill/vote/work запрос не
становится действительным при следующем возвращении в Exploration. Commands
после GameOver не исполняют gameplay. Авторитетный terminal result завершает
match-lifetime Runtime; `FinishObjective` завершает только отдельный episode.

Pure authority tests проверяют report/proximity, cooldown/limits, остановку
движения и работы, discussion communication, один ballot, replay, tie/Skip,
abstention, ejection, изменения roll-call, void forfeited ballot, Crew victory
и sabotage pause/resume. Фактические multiplayer/Codex результаты публикуются
отдельно в [отчёте Milestone 7](social-deduction-validation/README.md).
