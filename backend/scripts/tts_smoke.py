"""火山 TTS 全链路冒烟脚本（运维 / 上线前自检用）。

用途
----
在火山引擎语音技术控制台拿到 **AppID + Access Token** 后，用一条命令验证
「本服务 -> 火山 TTS」的真实链路：凭据是否有效、默认音色是否能合成、返回
音频是否完整。脚本**独立运行**（不 import pytest，netguard 管不到它），
会真实发起一次火山 HTTP 请求，失败时打印火山返回的 code 与 message。

用法
----
.. code-block:: bash

    cd backend
    export TTS_VOLC_APP_ID="你的AppID"                # 火山引擎语音技术控制台申请
    export TTS_VOLC_ACCESS_TOKEN="你的AccessToken"    # 同一个控制台申请
    python scripts/tts_smoke.py

退出码：0 = 合成成功；1 = 合成失败（已打印火山 code/message）；2 = 凭据未配置。

说明：
    * 脚本会**强制关闭降级**（TTS_FALLBACK_ENABLED=false），火山失败时直接
      暴露真实错误，不会静默回落到 edge——这正是冒烟想要的效果；
    * 成功时音频落盘到 ``backend/.qa_cache/tts_volc_smoke_<日期>.mp3``，
      可本地播放试听确认音色；
    * 不传 ``TTS_VOLC_ACCESS_TOKEN`` 时**不会**复用 ``ARK_API_KEY``：冒烟
      就是要验证控制台申请的这把专用 Token，缺了就明说（退出码 2）。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Tuple

# scripts/ 子目录独立运行：把 backend/ 根目录塞进 sys.path（与 smoke_dna003.py 同款）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 先于 import app 固定「关闭降级」：火山失败必须暴露真实错误，不能静默回落 edge
os.environ["TTS_FALLBACK_ENABLED"] = "false"

from app.core.config import settings  # noqa: E402
from app.services import tts_service  # noqa: E402

#: 冒烟固定文本（与线上读题语气一致的一条短句）
SMOKE_TEXT: str = "小朋友，这道题我们一起来看看"

#: 火山错误信息形态：``... code=45000000 message=xxx``（见 VolcTtsProvider.synthesize）
_VOLC_ERROR_PATTERN = re.compile(r"code=(?P<code>\S+)\s+message=(?P<message>.*)$", re.S)

#: 音频产物目录：backend/.qa_cache/
_QA_CACHE_DIR: Path = Path(__file__).resolve().parents[1] / ".qa_cache"


def _require_credentials() -> Tuple[str, str]:
    """从环境变量读取火山凭据；缺任一打印中文错误并以退出码 2 结束。

    Returns:
        ``(app_id, access_token)`` 二元组（均已 strip）。

    Raises:
        SystemExit: 缺少 ``TTS_VOLC_APP_ID`` 或 ``TTS_VOLC_ACCESS_TOKEN``。
    """
    app_id = (os.environ.get("TTS_VOLC_APP_ID") or "").strip()
    token = (os.environ.get("TTS_VOLC_ACCESS_TOKEN") or "").strip()
    missing = [
        name
        for name, value in (("TTS_VOLC_APP_ID", app_id), ("TTS_VOLC_ACCESS_TOKEN", token))
        if not value
    ]
    if missing:
        print("[错误] 缺少环境变量：" + "、".join(missing))
        print("        请先在火山引擎语音技术控制台申请 AppID 与 Access Token，然后：")
        print("        cd backend && export TTS_VOLC_APP_ID=你的AppID "
              "TTS_VOLC_ACCESS_TOKEN=你的AccessToken && python scripts/tts_smoke.py")
        raise SystemExit(2)
    return app_id, token


def _print_volc_error(exc: BaseException) -> None:
    """把火山错误里的 code / message 拆出来打印；拆不出就打原句。"""
    text = str(exc)
    matched = _VOLC_ERROR_PATTERN.search(text)
    if matched:
        print(f"[失败] 火山返回 code={matched.group('code')} message={matched.group('message')}")
    else:
        print(f"[失败] {text}")


async def _smoke() -> int:
    """执行一次火山合成并落盘，返回进程退出码。"""
    app_id, token = _require_credentials()

    # 凭据显式注入 settings（环境变量优先级高于 .env，这里再钉一次，避免歧义）
    settings.tts_volc_app_id = app_id
    settings.tts_volc_access_token = token
    # 双保险：强制关降级（import 前已设环境变量，这里再钉一次运行期配置），
    # 确保暴露火山真实错误而不是静默回落 edge
    settings.tts_fallback_enabled = False

    voice = tts_service.default_voice(tts_service.PROVIDER_VOLC)
    cluster = (settings.tts_volc_cluster or tts_service.VOLC_DEFAULT_CLUSTER).strip()
    print(f"[信息] provider=volc voice={voice} cluster={cluster} "
          f"app_id=***{app_id[-4:]} text={SMOKE_TEXT!r}")

    try:
        audio, mime = await tts_service.synthesize(
            SMOKE_TEXT, provider=tts_service.PROVIDER_VOLC
        )
    except tts_service.TtsError as exc:
        # 含 TtsProviderUnavailable（缺依赖/缺凭据）与上游失败（带 code/message）
        _print_volc_error(exc)
        return 1
    except Exception as exc:  # 未预期异常：原样打出，避免静默成功
        print(f"[失败] 未预期异常: {exc!r}")
        return 1

    if not audio:
        print("[失败] 合成返回空音频")
        return 1

    # 火山成功码为 0 / 3000（VOLC_SUCCESS_CODES）；synthesize 不回传 code，
    # 能走到这里即代表 code 命中成功集合，按规范成功码 0 打印。
    print(f"[ok] provider=volc code=0 bytes={len(audio)} mime={mime}")

    _QA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _QA_CACHE_DIR / f"tts_volc_smoke_{date.today():%Y%m%d}.mp3"
    out_path.write_bytes(audio)
    print(f"[ok] 音频已保存: {out_path}（{mime}）")
    return 0


def main() -> int:
    """脚本入口：跑一次冒烟并转成进程退出码。"""
    return asyncio.run(_smoke())


if __name__ == "__main__":
    sys.exit(main())
