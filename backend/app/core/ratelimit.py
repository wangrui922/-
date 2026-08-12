"""简单内存限流器（滑动窗口）。

适用场景：
    * 验证码下发频率控制（60s 冷却 + 1h 上限）
    * 登录尝试频率控制（15min 内上限）

注意：
    * 进程级内存存储，重启清零；多进程部署时各 worker 独立计数
      （对小型部署足够，大规模上线可换 Redis）。
    * 线程安全（``Lock``）。
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock
from typing import Dict, List


class RateLimiter:
    """滑动窗口限流器。"""

    def __init__(self) -> None:
        self._buckets: Dict[str, List[float]] = defaultdict(list)
        self._lock: Lock = Lock()

    def check(self, key: str, max_count: int, window_sec: int) -> bool:
        """检查并记录一次事件。

        如果在 ``window_sec`` 秒内已记录次数 < ``max_count``，则记录并返回 True；
        否则返回 False（被限流，不记录）。

        Args:
            key: 限流键（如 ``"send:13800000000"``）。
            max_count: 窗口内最大允许次数。
            window_sec: 窗口大小（秒）。

        Returns:
            True = 允许通过（已记录），False = 被限流。
        """
        now = time.time()
        cutoff = now - window_sec
        with self._lock:
            # 清理窗口外的旧记录
            self._buckets[key] = [t for t in self._buckets[key] if t > cutoff]
            if len(self._buckets[key]) >= max_count:
                return False
            self._buckets[key].append(now)
            return True

    def remaining(self, key: str, max_count: int, window_sec: int) -> int:
        """查询剩余可用次数（不记录）。"""
        now = time.time()
        cutoff = now - window_sec
        with self._lock:
            self._buckets[key] = [t for t in self._buckets[key] if t > cutoff]
            return max(0, max_count - len(self._buckets[key]))

    def reset(self, key: str) -> None:
        """清除某个 key 的所有记录。"""
        with self._lock:
            self._buckets.pop(key, None)

    def clear_all(self) -> None:
        """清空全部记录（测试用）。"""
        with self._lock:
            self._buckets.clear()


# ---------------------------------------------------------------- 全局实例
#: 验证码下发限流器
code_send_limiter: RateLimiter = RateLimiter()

#: 登录尝试限流器
login_attempt_limiter: RateLimiter = RateLimiter()

#: 限流常量
SEND_CODE_COOLDOWN_SEC: int = 60       # 同一手机号 60s 内只能发 1 次
SEND_CODE_HOURLY_MAX: int = 5          # 同一手机号 1h 内最多 5 次
SEND_CODE_HOURLY_SEC: int = 3600
LOGIN_ATTEMPT_MAX: int = 10            # 同一手机号 15min 内最多 10 次尝试
LOGIN_ATTEMPT_SEC: int = 900


__all__ = [
    "RateLimiter",
    "code_send_limiter",
    "login_attempt_limiter",
    "SEND_CODE_COOLDOWN_SEC",
    "SEND_CODE_HOURLY_MAX",
    "SEND_CODE_HOURLY_SEC",
    "LOGIN_ATTEMPT_MAX",
    "LOGIN_ATTEMPT_SEC",
]
