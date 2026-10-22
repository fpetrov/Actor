# Вход Twitch для чтения чата

Один раз зарегистрируйте собственное приложение Twitch с типом **Public** и
внесите его публичный Client ID в поле `twitch_client_id` существующего
`~/.config/actor/broadcast.json`. Пустой ID останавливает команду до запроса
OAuth. Client secret, пароль аккаунта и stream key не нужны.

```sh
actorctl broadcast twitch-login --config ~/.config/actor/broadcast.json
```

Команда покажет официальный адрес `https://www.twitch.tv/activate` и временный
код в локальном терминале. Откройте адрес самостоятельно, войдите как
`fedorr_codex` и подтвердите только `user:read:chat`. Не публикуйте код; Ctrl-C
отменяет ожидание. Ожидание ограничено сроком кода Twitch (не более 30 минут,
плюс ограниченное завершение текущего запроса).

После проверки Twitch Client ID, аккаунта и точного scope команда сохраняет
access/refresh token одной парой в принадлежащий ACTOR элемент macOS Keychain
`twitch-token-pair`. Токены не выводятся и не записываются в конфигурацию,
аргументы запуска или JSON-отчёт. Ошибка проверки или отказ пользователя не
заменяет сохранённую пару. Если отказал Keychain, команда сообщает ошибку,
а не успешный вход.

Успешный JSON содержит `authenticated: true`, `token_saved: true` и
`eventsub_connected: false`: это подготовка авторизации. Команда не запускает
модель, OBS, игру, EventSub или публичную трансляцию. Используйте тот же config
для последующего Broadcast preview; работающий контроллер сам не перечитывает
новую пару, поэтому вход удобнее выполнять до его запуска. Если chat явно
выключен через `chat_enabled: false`, эта команда не меняет настройку.

У Twitch Device Flow поддерживает Public-клиент без client secret;
installed chatbot с аккаунтом владельца канала может читать чат по
`user:read:chat`: [Device Code Flow](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#device-code-grant-flow),
[Installed Chatbots](https://dev.twitch.tv/docs/chat/authenticating/#installed-chatbots),
[регистрация приложения](https://dev.twitch.tv/docs/authentication/register-app/).

Реальный OAuth/Keychain вход требует отдельного действия оператора. Проверки
реализации используют подставные HTTP-ответы и хранилище; они не подтверждают
фактическое подключение чата.
