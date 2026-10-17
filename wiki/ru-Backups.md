# Резервное копирование

## Что входит в архив

`data/backend/backups/network-control-YYYYMMDD-HHMMSS.tar.gz`:

| Файл | Содержимое |
|---|---|
| `manifest.json` | Версия формата, дата, количество строк по таблицам |
| `database.json` | Все таблицы PostgreSQL |
| `openbao.snap` | Raft-снапшот OpenBao (зашифрован ключом распечатки) |

Потоки ClickHouse в архив не входят — это аналитические данные с ограниченным сроком хранения.

## Создание

- Автоматически раз в сутки (если включено в настройках), хранится `backup_keep` последних копий.
- Вручную — **Резервные копии → Создать копию** или `docker compose exec backend python -m app.cli backup`.
- Скачивание — кнопка в списке (фиксируется в аудите).

Отдельно храните `openbao/keys/unseal.key`, root-токен OpenBao из мастера настройки, `data/backend/bootstrap.json`, `data/backend/approle/` и `.env`.

## Восстановление

1. Разверните проект, верните `.env`, `openbao/keys/unseal.key`, `data/backend/bootstrap.json` и `data/backend/approle/`.
2. Запустите `postgres` и `openbao`: `docker compose up -d postgres openbao`.
3. Восстановите OpenBao:

   ```bash
   tar xzf network-control-*.tar.gz openbao.snap
   docker cp openbao.snap nc-openbao:/tmp/
   docker exec -e BAO_ADDR=http://127.0.0.1:8200 \
     -e BAO_TOKEN=<root-токен, показанный мастером настройки> \
     nc-openbao bao operator raft snapshot restore -force /tmp/openbao.snap
   ```

4. Восстановите базу (таблицы очищаются и заполняются из архива):

   ```bash
   cp network-control-*.tar.gz data/backend/backups/
   docker compose run --rm backend python -m app.cli restore-db /data/backups/network-control-YYYYMMDD-HHMMSS.tar.gz
   ```

5. Запустите остальные сервисы: `docker compose up -d`.
