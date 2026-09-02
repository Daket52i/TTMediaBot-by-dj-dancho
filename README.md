# TTMediaBot — Russian Edition

Привет! Меня зовут **DJ Dancho**, и это мой форк [TTMediaBot](https://github.com/JoaoDEVWHADS/TTMediaBot) — медиа-бот для TeamTalk 5, переделанный специально для тех, у кого серверы в России.

> Оригинальный проект: [JoaoDEVWHADS/TTMediaBot](https://github.com/JoaoDEVWHADS/TTMediaBot)
> Форк: [Daket52i/TTMediaBot-by-dj-dancho](https://github.com/Daket52i/TTMediaBot-by-dj-dancho)

## Что изменено

- **Русские сервисы:** добавлены Rutube (видео) и Музфонд (музыка) — работают без VPN и блокировок
- **Убраны лишние сервисы:** Yandex Music и VK удалены (можно вернуть при необходимости)
- **TeamTalk SDK 5.8.1:** обновлена библиотека для стабильной работы
- **YouTube.js Bridge:** вместо yt-dlp используется persistent YouTube.js бридж — быстрее и стабильнее
- **Автозапуск и рестарт:** настроен systemd-сервис + ежедневный рестарт в 3:00 ночи

## Сервисы

| Сервис | Команда | Описание |
|--------|---------|----------|
| YouTube | `sv yt` | Видео с YouTube |
| YouTube Music | `sv ytm` | Музыка с YouTube Music |
| **Rutube** | `sv rt` | Видео с Rutube (API, без авторизации) |
| **Музфонд** | `sv mf` | Музыка с Музфонда (HTML-скрапинг) |

Переключение: отправьте боту `sv rt` или `sv mf`.

## Установка

### Быстрый старт (рекомендуется)

```bash
wget https://raw.githubusercontent.com/Daket52i/TTMediaBot-by-dj-dancho/master/install_git_clone.sh
chmod +x install_git_clone.sh
sudo ./install_git_clone.sh
```

Скрипт сам установит зависимости (Git, Docker, ffmpeg), склонирует репозиторий и запустит менеджер ботов.

### Альтернатива — локальная установка (без Docker)

```bash
git clone https://github.com/Daket52i/TTMediaBot-by-dj-dancho.git
cd TTMediaBot-by-dj-dancho
sudo ./install.sh
```

Установит Python venv, Node.js зависимости, ffmpeg и TeamTalk библиотеки напрямую на сервер.

## Конфигурация

1. Скопируйте шаблон конфига:
   ```bash
   cp config_default.json config.json
   ```

2. Отредактируйте `config.json` — заполните данные вашего TeamTalk сервера:
   ```json
   {
       "teamtalk": {
           "hostname": "ваш-сервер.ru",
           "tcp_port": 10333,
           "udp_port": 10333,
           "username": "bot",
           "password": "ваш_пароль",
           "channel": "/Канал"
       }
   }
   ```

3. Сервисы `rt` и `mf` уже добавлены в `config_default.json` — просто скопируйте и отредактируйте.

## Команды бота

### Основные
| Команда | Описание |
|---------|----------|
| `p [запрос]` | Искать и играть. Без аргумента — пауза/продолжить |
| `u [url]` | Играть по ссылке |
| `s` | Стоп |
| `n` | Следующий трек |
| `v [0-100]` | Громкость |
| `sv [имя]` | Переключить сервис (`sv yt`, `sv ytm`, `sv rt`, `sv mf`) |
| `m [режим]` | Режим воспроизведения |
| `dl` | Скачать текущий трек |
| `h` | Список всех команд |

### Админ
| Команда | Описание |
|---------|----------|
| `cn [имя]` | Сменить ник |
| `sc` | Сохранить конфиг |
| `rs` | Перезапустить бота |
| `ua +[user]` | Добавить админа |

Полный список команд: [см. original README](https://github.com/JoaoDEVWHADS/TTMediaBot/blob/master/README.md#-commands)

## Автозапуск и рестарт

Если установка была через Docker — автозапуск настроен автоматически.

Для ручной установки (systemd):
```bash
# Сервис уже создан в /home/bot/.config/systemd/user/TTMediaBot.service
# Автозапуск включен через loginctl enable-linger
# Ежедневный рестарт в 3:00 — через ttbot-restart.timer
```

Проверить статус:
```bash
su - bot -c 'XDG_RUNTIME_DIR=/run/user/1002 systemctl --user status TTMediaBot'
```

## Cookie для YouTube

Для работы YouTube/YouTube Music нужен файл `cookies.txt`:

1. Установите расширение [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt/bgaddhkoddajcdgocldbbfleckgcbcid) в браузере
2. Зайдите на youtube.com
3. Экспортируйте cookies через расширение
4. Положите файл на сервер и укажите путь в `config.json` → `services.yt.cookiefile_path`

> Rutube и Музфонд работают **без cookies**.

## Языки

Бот поддерживает: `ru`, `en`, `es`, `pt_BR`, `ar`, `tr`, `hu`, `id`

Смена языка: `cl ru`

## Лицензия

MIT License — см. [LICENSE](LICENSE)

---

Форк создан DJ Dancho для русскоязычного сообщества TeamTalk. Если есть вопросы или идеи — пишите issues в репозитории.
