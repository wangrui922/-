#!/bin/bash
# 数学教练 SQLite 数据库自动备份脚本（宿主机版，backup.py 的 shell 等价物）
# 用法：bash backup.sh（由 crontab 每天 02:00 触发）
# 说明：依赖宿主机 sqlite3 CLI；若宿主机无 sqlite3 CLI，请改跑 backup.py
#       （纯 Python 标准库实现，保留策略完全一致）。
#       推荐用 backup.py（纯 Python 无外部依赖），本脚本保留作为备选。
#
# 路径规则（环境变量覆盖，默认值与生产宿主机完全一致，不设变量时行为不变）：
#   COACH_BASE       项目根，默认 /home/ubuntu/coach
#   COACH_DB         DB 文件，默认 ${COACH_BASE}/runtime/data/coach.db
#   COACH_BACKUP_DIR 备份目录，默认 ${COACH_BASE}/backups
#   COACH_BACKUP_LOG 日志文件，默认 ${COACH_BASE}/logs/backup.log

BASE=${COACH_BASE:-/home/ubuntu/coach}
DB_FILE=${COACH_DB:-${BASE}/runtime/data/coach.db}
BACKUP_DIR=${COACH_BACKUP_DIR:-${BASE}/backups}
LOG=${COACH_BACKUP_LOG:-${BASE}/logs/backup.log}

mkdir -p ${BACKUP_DIR} ${BASE}/logs

NOW=$(date '+%Y-%m-%d %H:%M:%S')
TODAY=$(date '+%Y%m%d')
WEEK_NUM=$(date '+%Y-W%U')
DOW=$(date '+%w')

# 1. 一致性冷备份（sqlite3 .backup 处理锁与 WAL，比 cp 安全）
BACKUP_FILE=${BACKUP_DIR}/coach-${TODAY}.db
tmpfile="${BACKUP_FILE}.tmp"

if ! sqlite3 "${DB_FILE}" ".backup '${tmpfile}'" 2>/dev/null; then
    echo "${NOW} ALERT 备份写入失败: ${DB_FILE} -> ${tmpfile}" >> ${LOG}
    rm -f "${tmpfile}"
    exit 1
fi

mv "${tmpfile}" "${BACKUP_FILE}"

# 2. 完整性校验
CHECK=$(sqlite3 "${BACKUP_FILE}" "PRAGMA integrity_check;" 2>&1)
if [ "${CHECK}" != "ok" ]; then
    echo "${NOW} ALERT 备份校验失败 integrity_check=${CHECK} file=${BACKUP_FILE}" >> ${LOG}
    rm -f "${BACKUP_FILE}"
    exit 1
fi

SIZE=$(stat -c %s "${BACKUP_FILE}")
echo "${NOW} OK 备份成功 ${BACKUP_FILE} size=${SIZE} integrity=ok" >> ${LOG}

# 3. 周日额外保留周备（weekly 目录，永久保留供回溯）
if [ "${DOW}" = "0" ]; then
    WEEKLY_DIR=${BACKUP_DIR}/weekly
    mkdir -p ${WEEKLY_DIR}
    WEEKLY=${WEEKLY_DIR}/coach-${WEEK_NUM}.db
    cp "${BACKUP_FILE}" "${WEEKLY}"
    echo "${NOW} OK 周备保留 ${WEEKLY}" >> ${LOG}
fi

# 4. 清理：删除超过 7 天的 daily 备份（weekly 目录不受影响）
DELETED=$(find ${BACKUP_DIR} -maxdepth 1 -name 'coach-*.db' -mtime +7 -print)
if [ -n "${DELETED}" ]; then
    find ${BACKUP_DIR} -maxdepth 1 -name 'coach-*.db' -mtime +7 -delete
    echo "${NOW} OK 清理旧 daily 备份" >> ${LOG}
fi

exit 0
