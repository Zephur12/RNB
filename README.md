# РНБ WarDogs — Discord bot

Автоматизация Discord-сервера клана «РНБ» (WarDogs). Фаза 1: провижининг
структуры сервера одной командой.

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Заполни `.env`:

* `DISCORD_BOT_TOKEN` — токен бота из Discord Developer Portal.
* `DISCORD_GUILD_ID` — (опционально, для разработки) ID тестового сервера,
  чтобы slash-команды синхронизировались мгновенно, а не до часа.

### Чек-лист перед первым запуском (Фаза 0)

* [ ] Создано приложение и бот в Discord Developer Portal
* [ ] Бот приглашён на сервер с правами: Manage Channels, Manage Roles,
      Send Messages, Embed Links, Attach Files, Use Application Commands
* [ ] Роль бота в списке ролей сервера — ВЫШЕ всех ролей, которые он
      создаёт/раздаёт (иначе `create_role`/`create_channel` с overwrites
      будут падать с `Forbidden`)
* [ ] Токен лежит в `.env`, `.env` не закоммичен

## Запуск

```bash
python bot/main.py
```

## Команды

### `/setup-server`

Доступна только владельцу/администратору сервера. Читает
`bot/config/server_structure.yaml` и идемпотентно создаёт недостающие роли,
категории и каналы (с permission overwrites для каналов с `restricted_to`).
Повторный запуск ничего не дублирует — в конце присылает embed-отчёт: что
создано, что уже было, что не удалось (например, не хватило прав).

Конфиг можно редактировать и дополнять — при следующем запуске команда
добьёт недостающее.

## Структура

```
bot/
  main.py              # точка входа, загрузка cogs, синхронизация команд
  cogs/
    provisioning.py     # /setup-server
  config/
    server_structure.yaml
  data/                 # создаётся кодом; не коммитится (см. .gitignore)
```
