'use strict';

// Buildless and offline: the deck has no dependencies and makes no API calls.
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const slides = $$('.slide');
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
const pinnedSource = 'https://github.com/fpetrov/Actor/blob/aa5d46ec1fee4fbcab9292fc1f58f48fee3e1bb8/';
let current = 0;
let initialized = false;
let planTimer;
let exitTimer;
let messageTimer;
let planRunning = false;

const speakerNotes = [
  {
    time: '00:00—00:35 / ВВЕДЕНИЕ',
    title: 'ACTOR',
    paragraphs: [
      'ACTOR связывает внешний интеллект с игрой. Агент получает доступные факты и действует через типизированные возможности. Agents Arena — конкретная игра, на которой можно проверить этот цикл.',
      'Сначала покажем границу между решением и исполнением, затем сетевые связи, новый классификатор и трансляцию. Чертежные схемы на слайдах — объяснение архитектуры. Они не подключены к живому матчу.',
      'Срез исходников: aa5d46e, 23 октября 2026 года. На момент сверки все 1737 отслеживаемых файлов локального HEAD совпали по Git blob SHA с GitHub main. Версия исходников отличается от версии опубликованных RC1-артефактов.'
    ],
    sources: [['README / философия и текущие версии', 'README.md'], ['Последнее изменение / область условий плана', 'docs/executable-plans.md']]
  },
  {
    time: '00:35—01:15 / ПРИНЦИП',
    title: 'Три части агента',
    paragraphs: [
      'Brain выбирает следующий Skill, ограниченный план или завершение цели. Можно заменить провайдера модели или использовать детерминированный Brain. Core не привязан к конкретной модели или игровым ролям.',
      'Runtime собирает контекст, исполняет Skills, управляет правом отправлять команды и завершает действие при отмене. Большая модель не управляет физикой по кадрам.',
      'Сервер игры проверяет право на действие, физическое положение, видимость и результат. Принятие команды локальным мостом еще не означает, что игровое действие завершилось.'
    ],
    sources: [['Brain и цели', 'docs/brains.md'], ['Skill Engine', 'docs/skills.md'], ['Текущий Codex adapter', 'docs/codex.md']]
  },
  {
    time: '01:15—01:50 / КОНТРАКТЫ',
    title: 'Общий язык, разные миры',
    paragraphs: [
      'Core определяет сессию, обнаружение возможностей, порядок сообщений и исходы команд. В нем нет комнат, убийств, ролей или голосования.',
      'Standard — необязательные переиспользуемые расширения. В проекте есть движение и сообщения. Arena описывает свою игру отдельным набором Protobuf-типов.',
      'Манифест расширения содержит типы, версии и хеши схем. SDK регистрирует возможности игры. Runtime загружает расширения и согласует контракт. Версии Core, Runtime, игры и расширений независимы.'
    ],
    sources: [['Протокол ACTOR', 'docs/actor-protocol.md'], ['Расширения', 'docs/extensions.md'], ['Схемы и генерация', 'proto/README.md']]
  },
  {
    time: '01:50—02:40 / АРХИТЕКТУРА',
    title: 'Два контура связи',
    paragraphs: [
      'Первый контур локальный: Python Runtime слушает loopback как gRPC-сервер. Unity подключается к нему как клиент по одному двустороннему Protobuf-потоку. Мост защищен временным локальным токеном.',
      'Второй контур игровой: Unity-клиент связывается с Dedicated Server через Netcode for GameObjects и Unity Transport по UDP. Это протокол конкретной Arena, он не входит в общий контракт ACTOR SDK.',
      'Control Plane отдельно обслуживает HTTP API: создает лобби, выдает допуск, выделяет порт и запускает сервер матча. SQLite хранит метаданные. Runtime и адаптер Brain работают на компьютере агента. Codex вызывается как внешний провайдер модели.',
      'В текущем admission protocol 4 есть аутентифицированный resume в том же процессе клиента. Старые networking-документы могут описывать состояние до этой реализации. Слайды опираются на текущий код и README.'
    ],
    sources: [['Core bridge.proto', 'proto/actor/core/v1/bridge.proto'], ['SDK transport', 'packages/com.actor.transport.grpc/Runtime/GrpcActorTransport.cs'], ['Control Plane', 'src/Actor.ControlPlane/Api/Endpoints.cs'], ['Текущий reconnect', 'docs/reconnect.md']]
  },
  {
    time: '02:40—03:20 / ИСПОЛНЕНИЕ',
    title: 'План работает без постоянных вызовов модели',
    paragraphs: [
      'Brain может вернуть 1–4 последовательных SkillCall, до двух одноразовых условных реакций и один запасной шаг после ошибки. Runtime проверяет их при принятии и перед исполнением.',
      'Схема показывает простой Sandbox-план: перейти в Engineering, подождать две секунды, вернуться. Кнопка проигрывает схему за несколько секунд. Она не запускает игру, а время анимации не равно времени реального маршрута.',
      'Условия ограничены: nonempty для согласованного repeated/string поля наблюдения или события. Это не произвольная программа из текста. После последовательности, реакции или fallback управление возвращается Brain. Критическая смена состояния отменяет устаревший план.',
      'В текущем Codex один SDK-процесс живет в пределах Objective, но каждый decision использует свежий ephemeral thread. Непрерывность дают актуальное состояние, предыдущий SkillResult и ограниченный WorkingKnowledge.'
    ],
    sources: [['Исполняемые планы', 'docs/executable-plans.md'], ['Проверка и выбор условий', 'runtime/actor_runtime/plans.py'], ['ObjectiveRunner', 'runtime/actor_runtime/objectives.py'], ['Новая модель потоков Codex', 'docs/codex.md']]
  },
  {
    time: '03:20—04:15 / НОВОЕ В ПРОЕКТЕ',
    title: 'MiniLM: нужен ли пересмотр?',
    paragraphs: [
      'MiniLM v7 — дообученный BERT-энкодер, около 17,4 млн параметров. Это бинарный классификатор, он не пишет новый план. Вход включает событие, цель, текущий Skill, собственный контекст и действительно принятый план.',
      'Увидели тело. Если принятое условие уже предписывает report и классификатор разрешает продолжение, Runtime выполняет эту реакцию без нового обращения к Brain. Если план факт не учитывает, MiniLM может рекомендовать пересмотр. Выполнение покрытой реакции проверено Runtime-тестом с настоящими весами. Вариант без реакции проверен контрфактически на том же входе, а не вторым живым прогоном Router.',
      'Третий переключатель показывает критическую смену фазы. Она обрабатывается доверенным правилом, минуя нейросеть. Runtime отменяет устаревшую работу и начинает новый когнитивный цикл по игровой политике.',
      'Не каждый кадр вызывает классификатор. Обычно Router получает события. Новый план может дополнительно выбрать первое свежее собственное наблюдение для конкретного условия. Rules остаются провайдером по умолчанию, MiniLM подключается явно. Jev — отдельный необязательный внешний провайдер, не часть MiniLM.',
      'Показанные переходы — фиксированная учебная схема по проверенным сценариям. В браузере модель не запускается. v7 имеет статус development_only: улучшение win rate или конкретная экономия токенов пока не доказаны.'
    ],
    sources: [['MiniLM / устройство, статус, границы оценки', 'classifiers/minilm/README.md'], ['Интеграционный тест с настоящими весами', 'runtime/tests/test_minilm_integration.py'], ['EventRouter', 'runtime/actor_runtime/events.py'], ['Другие классификаторы', 'docs/classifiers.md']]
  },
  {
    time: '04:15—04:55 / СРЕДА ПРОВЕРКИ',
    title: 'Agents Arena',
    paragraphs: [
      'В Social Deduction 4–6 участников, один Impostor. Люди и агенты используют одни игровые правила. Crew выполняет задания и ищет Impostor. Impostor скрывает свою сторону, устраняет игроков и может вызвать аварию.',
      'Карта на слайде построена из публичных полигонов реальной станции: Main Hall, Engineering, Security, Storage и восточный коридор. Это интерактивный чертеж, без живых позиций и скрытых игровых данных.',
      'После сообщения о теле или вызова собрания игра переходит к обсуждению и голосованию. Сервер определяет результат и победу. Sandbox остается отдельным режимом для навигации, совместной калибровки и расследований.',
      'Кнопка показывает настоящий исторический снимок нативного клиента Game 0.9.0, а не мокап или подтверждение текущего RC1. Текущая версия исходников игры — 0.10.0.'
    ],
    sources: [['Игра и правила', 'docs/social-deduction.md'], ['Авторская геометрия карты', 'runtime/actor_agents_arena/social_layout.json'], ['Голосование', 'docs/voting.md'], ['Источник скриншота M8', 'docs/release-validation/fog/candidate-c1-main-hall-001-800x600.png']]
  },
  {
    time: '04:55—05:35 / ГРАНИЦЫ ЗНАНИЯ',
    title: 'Только доступные факты',
    paragraphs: [
      'Разрешения применяет сервер до отправки данных. Чужая скрытая роль, игрок за стеной или невидимое тело не попадают в обычный контекст агента. Это не просто затемнение уже полученных объектов.',
      'Рабочая память разделяет historical_observation, player_claim, hypothesis и contradiction. Последняя виденная позиция остается датированной историей, но не становится текущей позицией. Чужая реплика остается утверждением ее автора.',
      'Иллюстрация убирает условную стену. Она показывает только принцип line of sight. В игре дополнительно действуют радиус, собственное состояние, фаза и ограничения конкретного типа данных.'
    ],
    sources: [['Видимость и аудит', 'docs/visibility.md'], ['Социальное рассуждение', 'docs/social-reasoning.md'], ['WorkingKnowledge', 'runtime/actor_runtime/knowledge.py']]
  },
  {
    time: '05:35—06:10 / НЕСКОЛЬКО АГЕНТОВ',
    title: 'Отдельные контексты, общий мир',
    paragraphs: [
      'У каждого участника свои Runtime, мост, Brain, профиль и база памяти. Supervisor запускает процессы, но не сообщает агентам скрытые назначения, позиции или чужие решения.',
      'Общение идет через обычный игровой сервер. Он проверяет отправителя, получателя, канал, срок и допустимость сообщения. Агенты не используют общую память и не получают чужой контекст напрямую.',
      'Есть разные стадии: локальный мост принял запрос, сервер разрешил доставку, клиент подтвердил получение, Brain прочитал. ClientReceived не доказывает, что Brain понял сообщение или что его содержание верно. Анимация — учебная схема этих стадий.'
    ],
    sources: [['Изоляция нескольких агентов', 'docs/multi-agent.md'], ['Сообщения и подтверждения', 'docs/communication.md'], ['Приватность Runtime', 'runtime/actor_runtime/privacy.py']]
  },
  {
    time: '06:10—07:05 / ТРАНСЛЯЦИЯ',
    title: 'От наблюдения до Twitch',
    paragraphs: [
      'Комментатор — отдельный read-only контур, а не третья игровая роль рядом с Crew и Impostor. Отдельный windowed Unity observer получает узкий серверный BroadcastView с radius/LOS/viewport. Рендер подтверждает показанный кадр через PresentationAck.',
      'Через локальный gRPC факты попадают в Python. EvidenceLedger связывает их с подтвержденным изображением, SpokenLedger учитывает фактически завершенное воспроизведение. Scheduler выбирает момент для короткого комментария. Официальный Codex provider gpt-6-luna создает текст с точными ссылками на evidence.',
      'Текущий голос — Qwen3-TTS-12Hz-1.7B-CustomVoice BF16 через MLX Audio, Ryan/Russian. Постоянный worker отдает 24 kHz mono F32LE. Browser Source и AudioWorklet направляют звук в OBS. Изображение берется из точного окна наблюдателя. Затем OBS отправляет эфир на Twitch.',
      'Guardian, ограниченные бюджеты, владение ресурсами и отзыв звука защищают трансляцию от устаревшего комментария и неверного захвата. Наличие кода не закрывает полную приемку. Реальные границы публичной попытки указаны на следующем слайде.'
    ],
    sources: [['Broadcast / архитектура и фактическая приемка', 'docs/broadcast.md'], ['Подтвержденный кадр и безопасность', 'docs/broadcast-renderer-safety.md'], ['Редакционный контур', 'docs/broadcast-editorial.md'], ['Текущий TTS worker', 'runtime/actor_broadcast/tts_worker.py']]
  },
  {
    time: '07:05—07:50 / РЕЗУЛЬТАТЫ',
    title: 'Что действительно проверено',
    paragraphs: [
      'Четыре реальных fixture-матча: два сценария, каждый с rules и MiniLM. Проверялись report/meeting/vote и выполнение восьми заданий. Во всех использовались детерминированные Brains, 0 LLM-вызовов. Это интеграционное доказательство, не оценка социальной стратегии.',
      'В двух матчах MiniLM: 85 neural assessments, p50 16,98 мс, p95 47,25 мс, максимум 51,24 мс на Apple M5 с четырьмя параллельными агентами. Включены projection и thread scheduling. Число 17 мс на слайде округлено.',
      'Публичная Twitch attempt04 достигла Live за 43,37 с. Inspector показал Excellent, 720p, около 4 Mbps. Но весь прогон получил FAIL на 137,97 с: суммарный resource Warning 61,927 с превысил предел 60 с. Cleanup подтвержден. Полный стабильный автономный матч в эфире пока не принят.',
      'Не заявляем 100% точности, улучшения win rate, фиксированной экономии токенов или production-ready Broadcast. Текущая v7 development validation участвовала в выборе модели. Исторические milestone-тесты не подменяют текущую приемку.'
    ],
    sources: [['MiniLM / четыре матча', 'classifiers/minilm/evidence/live-acceptance.json'], ['Производительность и условия', 'classifiers/minilm/evidence/performance.json'], ['Фактическая Twitch attempt04', 'docs/broadcast.md#validation-status']]
  },
  {
    time: '07:50—08:15 / ЗАВЕРШЕНИЕ',
    title: 'Система целиком',
    paragraphs: [
      'ACTOR дает общий контракт и исполняемый цикл агента. Agents Arena дает мир с физикой, скрытой информацией и другими участниками. Broadcast превращает доступные события в понятную зрителю историю.',
      'Ключевая идея: стратегическое решение отделено от непрерывного исполнения. Быстрый контур следит, когда надо вернуться к большой модели. Сервер остается источником игрового результата.',
      'Исходники презентации, исследовательские заметки и перечень источников находятся в папке presentation репозитория Actor. Презентация доступна по адресу /presentation/. Шрифты, схемы и изображения включены в поставку.'
    ],
    sources: [['ACTOR / исходный репозиторий', 'README.md'], ['MiniLM', 'classifiers/minilm/README.md'], ['Broadcast', 'docs/broadcast.md']]
  }
];

function selectInGroup(buttons, selected) {
  buttons.forEach(button => {
    const active = button === selected;
    button.classList.toggle('is-selected', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

const progress = $('#slide-progress');
const overviewList = $('#overview-list');
slides.forEach((slide, index) => {
  const button = document.createElement('button');
  button.setAttribute('aria-label', `${index + 1}. ${slide.dataset.title}`);
  button.title = slide.dataset.title;
  button.addEventListener('click', () => goTo(index));
  progress.append(button);
  const item = document.createElement('button');
  item.className = 'overview-item';
  const number = document.createElement('span');
  number.textContent = String(index + 1).padStart(2, '0');
  item.append(number, document.createTextNode(slide.dataset.title));
  item.addEventListener('click', () => { $('#overview').close(); goTo(index); });
  overviewList.append(item);
});

function renderNotes() {
  const note = speakerNotes[current];
  $('#notes-time').textContent = note.time;
  $('#notes-slide-title').textContent = note.title;
  $('#notes-copy').replaceChildren(...note.paragraphs.map(text => {
    const p = document.createElement('p');
    p.textContent = text;
    return p;
  }));
  $('#notes-sources').replaceChildren(...note.sources.map(([label, path]) => {
    const link = document.createElement('a');
    link.textContent = `${label} ↗`;
    link.href = pinnedSource + path;
    link.target = '_blank';
    link.rel = 'noopener';
    return link;
  }));
}

function stopPlan() {
  clearTimeout(planTimer);
  planRunning = false;
  $$('.plan-step').forEach(step => step.classList.remove('is-running'));
  $('#play-plan').innerHTML = '<span aria-hidden="true">▷</span> Пройти план';
}

function goTo(index, updateHash = true) {
  const next = Math.max(0, Math.min(slides.length - 1, index));
  if (next !== current && initialized) {
    const previous = slides[current];
    clearTimeout(exitTimer);
    slides.forEach(slide => slide.classList.remove('is-exiting'));
    previous.classList.remove('is-active');
    previous.classList.add('is-exiting');
    exitTimer = setTimeout(() => previous.classList.remove('is-exiting'), 450);
    if (current === 4) stopPlan();
  }
  current = next;
  slides.forEach((slide, i) => {
    const active = i === current;
    slide.classList.toggle('is-active', active);
    slide.inert = !active;
    slide.setAttribute('aria-hidden', String(!active));
    if (active) slide.scrollTop = 0;
  });
  document.body.classList.toggle('dark-mode', slides[current].classList.contains('dark'));
  document.querySelector('meta[name="theme-color"]').content = slides[current].classList.contains('dark') ? '#090c14' : '#1538f5';
  $('#current-page').textContent = String(current + 1).padStart(2, '0');
  $('#section-label').textContent = slides[current].dataset.section.toUpperCase();
  $('#prev').disabled = current === 0;
  $('#next').disabled = current === slides.length - 1;
  $$('#slide-progress button').forEach((button, i) => {
    button.classList.toggle('is-current', i === current);
    if (i === current) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
  });
  $$('.overview-item').forEach((item, i) => item.classList.toggle('is-current', i === current));
  $('.slide-announcement').textContent = `Слайд ${current + 1} из ${slides.length}. ${slides[current].dataset.title}`;
  document.title = `ACTOR / ${slides[current].dataset.title}`;
  if (updateHash && location.hash !== `#${current + 1}`) history.replaceState(null, '', `#${current + 1}`);
  renderNotes();
  initialized = true;
}

function fromHash() {
  const n = Number(location.hash.slice(1));
  goTo(Number.isInteger(n) && n > 0 && n <= slides.length ? n - 1 : 0, false);
}
window.addEventListener('hashchange', fromHash);
$('#prev').addEventListener('click', () => goTo(current - 1));
$('#next').addEventListener('click', () => goTo(current + 1));
$$('[data-next]').forEach(button => button.addEventListener('click', () => goTo(current + 1)));

function toggleDialog(dialog) {
  if (dialog.open) { dialog.close(); return; }
  $$('dialog[open]').forEach(open => open.close());
  dialog.showModal();
  dialog.append(crosshair);
  if (dialog.id === 'notes') renderNotes();
}
$('#index-trigger').addEventListener('click', () => toggleDialog($('#overview')));
$('#notes-trigger').addEventListener('click', () => toggleDialog($('#notes')));
$('#show-native').addEventListener('click', () => toggleDialog($('#native-preview')));
$$('dialog').forEach(dialog => {
  $('[data-close]', dialog).addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    ($('dialog[open]') || document.body).append(crosshair);
    if (dialog.id === 'overview') $('#index-trigger').setAttribute('aria-expanded', 'false');
    if (dialog.id === 'notes') $('#notes-trigger').setAttribute('aria-expanded', 'false');
  });
  const observer = new MutationObserver(() => {
    if (dialog.id === 'overview') $('#index-trigger').setAttribute('aria-expanded', String(dialog.open));
    if (dialog.id === 'notes') $('#notes-trigger').setAttribute('aria-expanded', String(dialog.open));
  });
  observer.observe(dialog, { attributes: true, attributeFilter: ['open'] });
});

async function fullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen();
    else if (document.documentElement.webkitRequestFullscreen) document.documentElement.webkitRequestFullscreen();
  } catch {
    $('#fullscreen').title = 'Полный экран недоступен в этом окне. Открой презентацию в браузере.';
  }
}
$('#fullscreen').addEventListener('click', fullscreen);
document.addEventListener('keydown', event => {
  if (event.altKey || event.ctrlKey || event.metaKey || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName) || event.target.isContentEditable) return;
  if (event.key === 'Escape') return;
  if ($('#overview').open || $('#native-preview').open) return;
  if (event.key === 'ArrowRight' || event.key === 'PageDown') { event.preventDefault(); goTo(current + 1); }
  else if (event.key === 'ArrowLeft' || event.key === 'PageUp') { event.preventDefault(); goTo(current - 1); }
  else if (event.key === 'Home') { event.preventDefault(); goTo(0); }
  else if (event.key === 'End') { event.preventDefault(); goTo(slides.length - 1); }
  else if (event.code === 'Space' && !/BUTTON|A/.test(event.target.tagName) && !$('#notes').open) { event.preventDefault(); goTo(current + (event.shiftKey ? -1 : 1)); }
  else if (['n', 'т'].includes(event.key.toLowerCase())) { event.preventDefault(); toggleDialog($('#notes')); }
  else if (['o', 'щ'].includes(event.key.toLowerCase())) { event.preventDefault(); toggleDialog($('#overview')); }
  else if (['f', 'а'].includes(event.key.toLowerCase())) { event.preventDefault(); fullscreen(); }
});

let touchStart;
$('#deck').addEventListener('touchstart', event => {
  if (event.touches.length !== 1 || event.target.closest('button,a,[role="button"]')) return;
  touchStart = { x: event.touches[0].clientX, y: event.touches[0].clientY, at: performance.now() };
}, { passive: true });
$('#deck').addEventListener('touchend', event => {
  if (!touchStart) return;
  const start = touchStart;
  touchStart = null;
  const end = event.changedTouches[0];
  const dx = end.clientX - start.x;
  const dy = end.clientY - start.y;
  if (Math.abs(dx) > 65 && Math.abs(dx) > Math.abs(dy) * 1.6 && performance.now() - start.at < 700) goTo(current + (dx < 0 ? 1 : -1));
}, { passive: true });

const parts = {
  brain: 'Brain получает контекст и выбирает типизированный Skill или план.',
  runtime: 'Runtime исполняет план, собирает результаты и останавливает устаревшие действия.',
  game: 'Сервер игры проверяет права, видимость, физику и итог каждого действия.'
};
$$('[data-part]').forEach(button => button.addEventListener('click', () => {
  selectInGroup($$('[data-part]'), button);
  $('#part-detail').textContent = parts[button.dataset.part];
}));
const contracts = {
  core: 'Core связывает агента с игрой. Правила конкретного мира остаются в расширениях.',
  standard: 'Необязательные общие пакеты: движение, координаты и сообщения. Их можно использовать в других играх.',
  arena: 'Расширение игры: собственная роль, видимые игроки, задания, собрания и результат матча.'
};
$$('[data-contract]').forEach(button => button.addEventListener('click', () => {
  selectInGroup($$('[data-contract]'), button);
  $$('[data-contract]').forEach(row => $('.row-plus', row).textContent = row === button ? '−' : '+');
  $('#contract-detail').textContent = contracts[button.dataset.contract];
}));
$$('[data-route]').forEach(button => button.addEventListener('click', () => {
  selectInGroup($$('[data-route]'), button);
  const observation = button.dataset.route === 'observation';
  $('.connection-board').dataset.routeView = button.dataset.route;
  $('#route-detail').textContent = observation
    ? 'Сервер отбирает доступные факты. Unity передает их в локальный Runtime.'
    : 'Brain выбирает Skill. Runtime отправляет команду через Unity. Сервер проверяет и исполняет ее.';
}));

const planDetails = [
  'Навигация сама ведет персонажа и ждет подтверждения прибытия.',
  'Runtime выдерживает паузу. В этот момент новый запрос к Brain не нужен.',
  'Навигация возвращает агента в Main Hall. После плана управление получит Brain.'
];
function setPlanStep(step) {
  const steps = $$('[data-step]');
  selectInGroup(steps, steps[step]);
  $('#plan-detail').textContent = planDetails[step];
}
$$('[data-step]').forEach(button => button.addEventListener('click', () => { stopPlan(); setPlanStep(Number(button.dataset.step)); }));
$('#play-plan').addEventListener('click', () => {
  if (planRunning) { stopPlan(); return; }
  planRunning = true;
  $('#play-plan').innerHTML = '<span aria-hidden="true">Ⅱ</span> Остановить';
  let step = 0;
  const tick = () => {
    $$('.plan-step').forEach(element => element.classList.remove('is-running'));
    if (step === 3) {
      stopPlan();
      $('#plan-detail').textContent = 'План завершен. Runtime передает результат Brain для следующего решения.';
      return;
    }
    setPlanStep(step);
    $$('.plan-step')[step].classList.add('is-running');
    step += 1;
    planTimer = setTimeout(tick, 1200);
  };
  tick();
});

const scenarios = {
  covered: ['ПЛАН УЧИТЫВАЕТ ФАКТ', '↳', 'Выполнить реакцию', 'Runtime сообщает о теле по принятому плану. Новый вызов Brain пока не нужен.'],
  uncovered: ['MiniLM → ПЕРЕСМОТР', '↻', 'Вернуться к Brain', 'В плане нет подходящей реакции. Runtime останавливает действие и запрашивает новое решение.'],
  critical: ['КРИТИЧЕСКОЕ ПРАВИЛО', '!', 'Отменить старый план', 'Сменилась фаза игры. Правило сразу останавливает устаревшую работу, мимо MiniLM.']
};
$$('[data-scenario]').forEach(button => button.addEventListener('click', () => {
  selectInGroup($$('[data-scenario]'), button);
  const [label, symbol, title, description] = scenarios[button.dataset.scenario];
  $('#gate-label').textContent = label;
  const icon = $('#gate-symbol');
  icon.replaceChildren();
  if (button.dataset.scenario === 'uncovered') {
    const retry = svgElement('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true' });
    retry.append(svgElement('path', { d: 'M3 12a9 9 0 1 0 2.64-6.36L3 8' }));
    retry.append(svgElement('path', { d: 'M3 3v5h5' }));
    icon.append(retry);
  } else {
    icon.textContent = symbol;
  }
  $('#gate-title').textContent = title;
  $('#gate-description').textContent = description;
  $('.event-input>strong').textContent = button.dataset.scenario === 'critical' ? 'Началось собрание.' : 'Агент увидел тело.';
  const output = $('.gate-output');
  output.classList.remove('change');
  void output.offsetWidth;
  output.classList.add('change');
}));

// Exact public floor polygons. This is an architectural map, not a game feed.
const SVG_NS = 'http://www.w3.org/2000/svg';
function svgElement(tag, attributes = {}, text = '') {
  const element = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  if (text) element.textContent = text;
  return element;
}
const map = $('#station-map');
const px = x => 115 + (x + 4.5) * 31;
const py = y => 22 + (6.4 - y) * 31;
const grid = svgElement('g', { class: 'map-grid', 'aria-hidden': 'true' });
for (let x = 115; x < 560; x += 31) grid.append(svgElement('path', { d: `M${x} 22V600` }));
for (let y = 22; y < 600; y += 31) grid.append(svgElement('path', { d: `M115 ${y}H560` }));
map.append(grid);
const roomInfo = {
  'main-hall': ['01 / MAIN HALL', 'Главный зал. Собрания, задания и точка встречи.', -.1, -1.3],
  engineering: ['02 / ENGINEERING', 'Инженерный отсек. Задания и система жизнеобеспечения.', 0, -7.5],
  security: ['03 / SECURITY', 'Служба безопасности. Задание у терминала записей.', 6.4, 3.8],
  storage: ['04 / STORAGE', 'Склад. Задание по учету запасов.', 5.2, -6.9]
};
STATION.floors.filter(floor => floor.room_id === 'east-corridor').forEach(floor => {
  map.append(svgElement('polygon', { class: 'floor corridor', points: floor.physical_polygon.map(p => `${px(p.x)},${py(p.y)}`).join(' '), 'aria-hidden': 'true' }));
});
Object.entries(roomInfo).forEach(([id, [name, description, x, y]]) => {
  const group = svgElement('g', { class: 'room', role: 'button', tabindex: '0', 'aria-label': description, 'aria-pressed': 'false', 'data-room': id });
  STATION.floors.filter(floor => floor.room_id === id).forEach(floor => group.append(svgElement('polygon', { class: 'floor', points: floor.physical_polygon.map(p => `${px(p.x)},${py(p.y)}`).join(' ') })));
  group.append(svgElement('text', { x: px(x), y: py(y) - 8, 'text-anchor': 'middle', class: 'map-room-number' }, name.split(' / ')[0]));
  group.append(svgElement('text', { x: px(x), y: py(y) + 12, 'text-anchor': 'middle' }, name.split(' / ')[1]));
  const select = () => {
    $$('.room', map).forEach(room => { room.classList.toggle('is-selected', room === group); room.setAttribute('aria-pressed', String(room === group)); });
    $('#room-detail').textContent = description;
  };
  group.addEventListener('click', select);
  group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } });
  map.append(group);
});
map.append(svgElement('path', { class: 'map-route', d: `M${px(0)} ${py(-2.5)}V${py(-7.5)}`, 'aria-hidden': 'true' }));
map.append(svgElement('circle', { cx: px(0), cy: py(-2.5), r: '4', fill: '#f5f4ec', 'aria-hidden': 'true' }));
map.append(svgElement('path', { d: `M${px(0)-4} ${py(-7.5)+6}L${px(0)} ${py(-7.5)}L${px(0)+4} ${py(-7.5)+6}`, fill: 'none', stroke: '#f5f4ec', 'stroke-width': '1', transform: `rotate(180 ${px(0)} ${py(-7.5)})`, 'aria-hidden': 'true' }));

$('#toggle-sight').addEventListener('click', () => {
  const open = $('#sight-stage').classList.toggle('is-open');
  $('#toggle-sight').setAttribute('aria-pressed', String(open));
  $('#toggle-sight').innerHTML = `${open ? 'Закрыть линию видимости' : 'Открыть линию видимости'} <span aria-hidden="true">↗</span>`;
  $('#sight-title').textContent = open ? 'Игрок виден — факт доступен.' : 'За стеной — нет факта.';
  $('#sight-description').textContent = open ? 'Если сервер разрешает наблюдение, оно попадает в контекст. Чужая скрытая роль остается закрытой.' : 'Сервер не передает скрытого игрока. Интерфейс не может раскрыть его случайно.';
});

$('#send-message').addEventListener('click', () => {
  clearTimeout(messageTimer);
  const world = $('.shared-world');
  world.classList.remove('is-sending');
  void world.offsetWidth;
  world.classList.add('is-sending');
  $('#message-detail').textContent = 'Агент A отправил утверждение через сервер. Агент B получает его с именем автора.';
  messageTimer = setTimeout(() => {
    world.classList.remove('is-sending');
    $('#message-detail').textContent = 'Клиент подтвердил доставку. Прочитал ли сообщение Brain — отдельный факт.';
  }, 2200);
});

const broadcastDetails = {
  observer: ['READ-ONLY / OBSERVER', 'Наблюдатель получает отдельный разрешенный вид мира. В игре он не действует.'],
  commentator: ['EVIDENCE → CODEX', 'Scheduler выбирает момент. Модель пишет короткий комментарий по фактам с подтвержденного кадра.'],
  voice: ['QWEN3-TTS / MLX AUDIO', 'Постоянный локальный worker превращает текст в звук. Голос поступает в Browser Source OBS.'],
  stream: ['WINDOW CAPTURE + AUDIO', 'OBS захватывает точное окно наблюдателя, добавляет голос и отправляет эфир на Twitch.']
};
$$('[data-broadcast]').forEach(button => button.addEventListener('click', () => {
  selectInGroup($$('[data-broadcast]'), button);
  const [label, detail] = broadcastDetails[button.dataset.broadcast];
  $('#broadcast-detail-label').textContent = label;
  $('#broadcast-detail').textContent = detail;
}));
[12, 25, 39, 22, 45, 31, 17, 35, 24, 13].forEach((height, i) => {
  const bar = document.createElement('i');
  bar.style.height = `${height}px`;
  bar.style.animationDelay = `${i * -.11}s`;
  $('.waveform').append(bar);
});

const evidenceDetails = {
  fixtures: '4 матча: 2 сценария × rules/MiniLM. Brains детерминированные, 0 вызовов LLM. Проверена интеграция, а не качество стратегии.',
  latency: 'p50 16,98 мс · p95 47,25 мс · max 51,24 мс. Apple M5, 4 агента. Включены подготовка входа и запуск в потоке. Это не задержка всей LLM.',
  broadcast: 'Live за 43,37 с, 720p, около 4 Mbps. Весь прогон: FAIL по ресурсу Warning > 60 с. Запуск эфира подтвержден, полный Broadcast еще не принят.'
};
$$('[data-evidence]').forEach(button => button.addEventListener('click', () => {
  selectInGroup($$('[data-evidence]'), button);
  $('#evidence-detail').textContent = evidenceDetails[button.dataset.evidence];
}));

// The crosshair replaces the native cursor only while it is visible and supported.
const crosshair = $('.crosshair');
const cursorMedia = matchMedia('(min-width:601px) and (hover:hover) and (pointer:fine) and (prefers-reduced-motion:no-preference)');
let pointerX = -100, pointerY = -100, pointerFrame = 0;
function restoreNativeCursor() {
  cancelAnimationFrame(pointerFrame);
  pointerFrame = 0;
  document.documentElement.classList.remove('custom-cursor-active');
  crosshair.style.opacity = '0';
  crosshair.classList.remove('is-click', 'is-hover');
}
document.addEventListener('pointermove', event => {
  if (!cursorMedia.matches || event.pointerType === 'touch') {
    restoreNativeCursor();
    return;
  }
  pointerX = event.clientX;
  pointerY = event.clientY;
  if (!pointerFrame) pointerFrame = requestAnimationFrame(() => {
    crosshair.style.left = `${pointerX}px`;
    crosshair.style.top = `${pointerY}px`;
    crosshair.style.opacity = '0.65';
    document.documentElement.classList.add('custom-cursor-active');
    pointerFrame = 0;
  });
  crosshair.classList.toggle('is-hover', Boolean(event.target.closest('button,a,[role="button"],.interactive-surface')));
}, { passive: true });
document.addEventListener('pointerdown', () => crosshair.classList.add('is-click'));
document.addEventListener('pointerup', () => crosshair.classList.remove('is-click'));
document.addEventListener('pointercancel', restoreNativeCursor);
document.documentElement.addEventListener('pointerleave', restoreNativeCursor);
window.addEventListener('blur', restoreNativeCursor);
cursorMedia.addEventListener('change', () => { if (!cursorMedia.matches) restoreNativeCursor(); });

// Set actual initial state; tab order never enters an inactive slide.
$$('[data-part],[data-contract],[data-step],[data-broadcast]').forEach(button => button.setAttribute('aria-pressed', String(button.classList.contains('is-selected'))));
fromHash();
