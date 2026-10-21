# Устранение игроков в SocialDeduction

Эти правила принадлежат Agents Arena. Legacy Sandbox/Investigation продолжает
использовать прежние механики. Источник истины — отдельная серверная
`SocialMatchAuthority`; ACTOR Core не содержит игровых понятий убийства,
принадлежности или смерти.

## Раздельные состояния

Сервер хранит Duty (`Crew`, `Engineer`, `Sheriff`), Alignment (`Crew`, `Impostor`)
и Life (`Alive`, `Dead`, `Ejected`, `Disconnected`, `Spectator`) отдельно.
Обычный матч назначает одну скрытую Impostor-принадлежность среди 4–6 готовых
участников через CSPRNG. Duty распределяется независимо. Собственные поля
доставляются владельцу; visible actor не содержит чужую Duty или Alignment.
Trusted seed существует только для серверных fixtures.

## Запрос убийства

`SocialKillCommand` проходит существующий путь Runtime → gRPC → owner NGO RPC →
Dedicated Server. Отправителя сервер определяет по одобренному соединению и
его player object. Контекст проверяет match identity, текущую phase revision,
correlation и срок действия до 10 секунд относительно серверного времени.

Authority проверяет живого назначенного Impostor, Exploration, cooldown,
отсутствие собственной незавершённой работы, другую живую цель, свежие конечные
физические позиции, дистанцию и серверную видимость/LOS. Позиция — центр
фактической капсулы, отдельно от actor root и предсказанного visual.
По умолчанию kill distance — 0.9, cooldown — 30 секунд, включая начальную
задержку после назначения ролей. Значения поступают из immutable configuration.

Неизвестная, невидимая, закрытая стеной и слишком далёкая корректно оформленная
цель дают одинаковый `target_unavailable`. Ошибки не сообщают точную дистанцию
или скрытую позицию. Проверки размера, bounded identifiers, owner RPC budget,
per-actor/global authority budgets и typed correlation replay ограничивают
перебор и повторные действия.

## Смерть и свидетель

Успешное действие меняет Life жертвы на `Dead`, отменяет её работу и создаёт
server-generated BodyId в фактической позиции. Собственный typed life event
останавливает живую деятельность Runtime. Серверные movement gates блокируют
дальнейшее движение; dead actor не может работать, голосовать, убивать или
переписываться с живыми.

`SocialVisibleAttackEvent` получает только живой сторонний наблюдатель, который
видел одновременно атакующего и жертву непосредственно перед действием.
Жертва получает собственное состояние, атакующий — результат запроса и
собственный cooldown. Удалённому наблюдателю невидимое убийство не меняет
public match revision, own revision, roster или доступный список тел.

Тело содержит victim identity/name и позицию, без killer, Alignment, Duty или
глобального времени смерти. Оно реплицируется и попадает в visible world только
по серверной interest policy. Внутренний `BodyStates()` предназначен исключительно
серверному body spawner; персональный `Bodies(observer)` фильтруется видимостью.
После report соответствующее тело исключается из доступных тел.

## Disconnect и terminal cleanup

Потеря игрового соединения или `RuntimeUnavailable` означает необратимый
forfeit: `Disconnected`, отмена работы и пересчёт победы. Тело при forfeit
не создаётся. Уже Dead/Ejected участник сохраняет свой terminal life state.
Новый ActorId после начала матча становится неподвижным ограниченным Spectator.
Имя игрока не восстанавливает прежнюю принадлежность; повторное использование
того же ActorId с другим connection epoch также не возрождает участника.

После GameOver итоговые Life/Alignment/Duty и summary заморожены. Поздний
disconnect не переписывает результат. Summary отдельно показывает командную
победу, личное выживание и длительность жизни: погибший Crew может принадлежать
победившей команде.

## Порядок победы и проверка

Сервер обрабатывает истёкшие дедлайны по времени. При одном timestamp приоритет:
match timeout (draw), затем sabotage timeout, переход фазы, затем завершение
физической работы. Поэтому завершённый до аварийного дедлайна repair сохраняет
силу даже при позднем tick; repair ровно на дедлайне опаздывает. После обычного
изменения состояния проверяются отсутствие живых участников (draw), устранение
всех Impostors, task threshold и Impostor parity. Первый terminal результат
необратим.

Чистые тесты `tests/Actor.Sdk.Tests/SocialMatchAuthorityTests.cs` покрывают
permissions, range/LOS, одинаковые отказы, свидетелей, невидимое убийство,
персональные revisions, тела, terminal life, forfeit/reconnect, replay и гонки
дедлайнов. Реальную NGO replication и live-проверки следует оценивать по
[отчёту Milestone 7](social-deduction-validation/README.md); unit-тесты сами по
себе не доказывают сетевую невидимость.
