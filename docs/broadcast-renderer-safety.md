# Safe-экран в BrowserSource

BrowserSource `ACTOR Voice` содержит независимый от Python-контроллера
непрозрачный Safe/Disconnected экран. Это **визуальный Safe**, а не команда
`StopStream`: OBS может продолжать трансляцию этого экрана.

HTML показывает Safe с первого кадра, ещё до JavaScript и проверки capability.
Renderer скрывает экран только при открытом авторизованном соединении, корректном
heartbeat моложе одной секунды, загруженном AudioWorklet с созданным узлом и
`AudioContext.state === "running"`. Один heartbeat не обходит отказ autoplay,
ошибку загрузки worklet или приостановленный audio context.

Таймер проверяет состояние каждые 250 мс. После порога одной секунды без
heartbeat ближайшая проверка возвращает непрозрачный Safe, очищает субтитры и
посылает существующую команду `lost` в worklet. Закрытие/ошибка WebSocket,
некорректный кадр, ошибка процессора и приостановка context также закрывают
изображение. Терминальная ошибка соединения не снимается поздним heartbeat.

`lost` использует существующую очистку очереди/истории worklet. Отчёты
`progress`, `complete`, `interrupted`, `flushed` по-прежнему исходят только из
реального процессора; таймер, heartbeat и показ Safe не создают playback ACK.
Worklet сохраняет отдельный звуковой deadman. Эти механизмы не доказывают
слышимость у зрителя Twitch.

## Обязательная композиция OBS

В `ACTOR Match` BrowserSource должен занимать весь canvas 1280×720, находиться
**выше** `ACTOR Observer Window`, иметь обычное непрозрачное смешивание, без
crop/rotation/scale/фильтров, уменьшающих покрытие. Production prepare создаёт
VIDEO первым, VOICE вторым; тест фиксирует этот порядок. Сам по себе этот тест
не подтверждает текущие индексы/transform/blend mode в работающем OBS — это
отдельная проверка графа. Нельзя объявлять Safe доказанным по одному наличию HTML.

Граница защиты: renderer должен оставаться загруженным, а его JavaScript event
loop — исполняться. Зависание Chromium/OBS, перезагрузка BrowserSource при уже
недоступном локальном HTTP-сервере или изменение композиции требуют отдельного
наблюдения. Таймер не получает OBS-права, пароли или право запускать процессы.

## Проверки

```sh
PYTHONPATH=runtime:generated/python build/runtime-venv/bin/python -m pytest \
  runtime/tests/test_broadcast_renderer.py runtime/tests/test_broadcast_worklet.py -q
```

Node исполняет фактический packaged renderer в подставном DOM/WebAudio/WebSocket
окружении с управляемыми часами: исходный Safe, autoplay, отложенный/ошибочный
graph, открытый зависший socket, устаревший heartbeat, закрытие, ошибки кадров,
приостановка context и неизменённый ACK. Отдельный тест исполняет настоящий
worklet с синтетическими render-квантами. Реальный контроллерный fault-тест ещё
должен подтвердить кадром/записью OBS, что Safe полностью закрывает game capture;
эти проверки без браузера и устройства его не подменяют.
