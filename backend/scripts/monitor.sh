#!/bin/bash
# 数学教练生产监控脚本（部署于宿主机）
# 每分钟由 crontab 触发，输出落盘到 ${COACH_BASE}/logs/
# 路径规则（环境变量覆盖，默认值与生产宿主机一致）：
#   COACH_BASE 项目根，默认 /home/ubuntu/coach
# 监控项：容器存活 / API 健康 / DB 大小突变 / 磁盘空间 / 后端日志 ERROR 频率
# 告警落 alert.log（tee 同时回显），明细落 health.log；alert.log 每日切割保留 7 天

BASE=${COACH_BASE:-/home/ubuntu/coach}
LOG_DIR=${BASE}/logs
HEALTH_LOG=${LOG_DIR}/health.log
ALERT_LOG=${LOG_DIR}/alert.log
DB_FILE=${BASE}/runtime/data/coach.db
COMPOSE_DIR=${BASE}/deploy

mkdir -p ${LOG_DIR}

NOW=$(date '+%Y-%m-%d %H:%M:%S')
TS=$(date '+%s')
ALERT_COUNT=0

echo "===== ${NOW} =====" >> ${HEALTH_LOG}

# 1. 容器存活检查
for c in deploy-backend-1 deploy-worker-1 deploy-redis-1 deploy-web-1 deploy-admin-web-1; do
    STATUS=$(docker ps --filter "name=${c}" --format '{{.Status}}' 2>/dev/null)
    if [ -z "${STATUS}" ]; then
        echo "ALERT [容器离线] ${c}" | tee -a ${ALERT_LOG}
        ALERT_COUNT=$((ALERT_COUNT+1))
    else
        echo "OK [容器] ${c}: ${STATUS}" >> ${HEALTH_LOG}
    fi
done

# 2. API 健康探测（在容器内部通过 localhost 访问，宿主机 8000 不暴露）
HEALTH_CODE=$(docker exec deploy-backend-1 sh -c "python3 -c 'import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(\"http://127.0.0.1:8000/api/health\",timeout=10).status == 200 else 1)'" 2>/dev/null && echo "200" || echo "000")
if [ "${HEALTH_CODE}" != "200" ]; then
    echo "ALERT [API不健康] /api/health HTTP ${HEALTH_CODE}" | tee -a ${ALERT_LOG}
    ALERT_COUNT=$((ALERT_COUNT+1))
else
    echo "OK [API] /app 内部 /api/health 200" >> ${HEALTH_LOG}
fi

# 3. DB 文件异常检测（大小突变/膨胀）
if [ -f "${DB_FILE}" ]; then
    DB_SIZE=$(stat -c %s "${DB_FILE}" 2>/dev/null)
    LAST_SIZE_FILE=${LOG_DIR}/.last_db_size
    if [ -f "${LAST_SIZE_FILE}" ]; then
        LAST_SIZE=$(cat ${LAST_SIZE_FILE})
        DELTA=$((DB_SIZE - LAST_SIZE))
        # 单次增长 >10MB 或总大小 >500MB 视为异常
        if [ ${DELTA} -gt 10485760 ] || [ ${DB_SIZE} -gt 524288000 ]; then
            echo "ALERT [DB异常] size=${DB_SIZE} delta=${DELTA}" | tee -a ${ALERT_LOG}
            ALERT_COUNT=$((ALERT_COUNT+1))
        else
            echo "OK [DB] size=${DB_SIZE} delta=${DELTA}" >> ${HEALTH_LOG}
        fi
    else
        echo "OK [DB] init size=${DB_SIZE}" >> ${HEALTH_LOG}
    fi
    echo ${DB_SIZE} > ${LAST_SIZE_FILE}
else
    echo "ALERT [DB丢失] ${DB_FILE} not found" | tee -a ${ALERT_LOG}
    ALERT_COUNT=$((ALERT_COUNT+1))
fi

# 4. 磁盘空间（阈值 85%）
DISK_USED=$(df -h / | awk 'NR==2 {print $5}' | tr -d '%')
if [ -n "${DISK_USED}" ] && [ "${DISK_USED}" -ge 85 ]; then
    echo "ALERT [磁盘] / used=${DISK_USED}%" | tee -a ${ALERT_LOG}
    ALERT_COUNT=$((ALERT_COUNT+1))
else
    echo "OK [磁盘] / used=${DISK_USED}%" >> ${HEALTH_LOG}
fi

# 5. 后端日志 ERROR 频率（最近 5 分钟）
BACKEND_LOG=$(docker logs --since 5m deploy-backend-1 2>&1 | grep -c "ERROR\|CRITICAL" || true)
if [ "${BACKEND_LOG}" -gt 20 ]; then
    echo "ALERT [日志] backend ERROR/CRITICAL in 5m: ${BACKEND_LOG}" | tee -a ${ALERT_LOG}
    ALERT_COUNT=$((ALERT_COUNT+1))
else
    echo "OK [日志] backend errors(5m)=${BACKEND_LOG}" >> ${HEALTH_LOG}
fi

echo "SUMMARY alert_count=${ALERT_COUNT}" >> ${HEALTH_LOG}

# 6. 告警日志轮转（每天切割，保留 7 天）
if [ "$(date '+%H:%M')" = "00:00" ]; then
    mv ${ALERT_LOG} ${LOG_DIR}/alert-$(date '+%Y%m%d').log 2>/dev/null || true
    touch ${ALERT_LOG}
    find ${LOG_DIR} -name 'alert-*.log' -mtime +7 -delete 2>/dev/null || true
fi

exit 0
