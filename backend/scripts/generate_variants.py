#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
错题变形题生成器（技术验证 / 可独立运行）
================================================
用途：给一道错题，调用本机可达的 DeepSeek，生成 N 道「同知识点、不同情境」的变形题，
      用于让孩子"练的还是同一个薄弱点，但感觉是新题"，避免枯燥。

运行环境：需能访问 DeepSeek API（国内可达，无需外网）。
依赖：pip install requests

用法：
  python generate_variants.py --question "题干" --kp "分数加减" --grade "五年级" --n 3
  或不带参数，使用示例错题直接演示。

密钥：优先读环境变量 DEEPSEEK_API_KEY；否则读 DeepSeekApp/config.json。
      密钥仅在内存中使用，不写入任何文件。
"""
import argparse
import json
import os
import sys
import urllib.request

try:
    import requests
except ImportError:
    requests = None

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"


def load_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    for path in (
        r"C:\Users\Administrator\DeepSeekApp\config.json",
        os.path.expanduser(r"~/.workbuddy/skills/yueyue-illustrations/config.json"),
    ):
        if os.path.exists(path):
            try:
                d = json.load(open(path, encoding="utf-8"))
                for k in ("api_key", "key", "DEEPSEEK_API_KEY"):
                    if d.get(k):
                        return d[k]
            except Exception:
                pass
    return ""


def build_prompt(grade: str, kp: str, wrong: str, n: int) -> str:
    return f"""你是小学{grade}数学老师。基于下面这道错题，生成 {n} 道同知识点「{kp}」的变形题。
要求：换主角 / 换数字 / 换生活场景，但练的还是同一个薄弱点。
每题输出 JSON 对象字段：stem（题干）、options（ABCD 四个选项）、answer（正确选项字母）、explain（一句解析）。
只输出一个 JSON 数组，不要任何解释文字。
错题：{wrong}"""


def call_deepseek(key: str, prompt: str, n: int):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.85,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if requests:
        r = requests.post(API_URL, json=body, headers=headers, timeout=40)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    else:
        req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=40) as resp:
            content = json.load(resp)["choices"][0]["message"]["content"]
    # DeepSeek json_object 有时包裹在 {"xxx":[...]} 里，做兜底解析
    data = json.loads(content)
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                data = v
                break
    return data[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default="一根绳子长 3/4 米，用掉 1/3 米，还剩多少米？学生答 1/3 米（错因：未通分直接相减）")
    ap.add_argument("--kp", default="分数加减")
    ap.add_argument("--grade", default="五年级")
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args()

    key = load_key()
    if not key:
        print("[错误] 未找到 DeepSeek Key。请设置环境变量 DEEPSEEK_API_KEY，或在 DeepSeekApp/config.json 中配置。")
        sys.exit(2)

    prompt = build_prompt(args.grade, args.kp, args.question, args.n)
    try:
        variants = call_deepseek(key, prompt, args.n)
    except Exception as e:
        print(f"[调用失败] {e}")
        sys.exit(3)

    print(f"\n✅ 生成 {len(variants)} 道「{args.kp}」变形题：\n")
    for i, q in enumerate(variants, 1):
        stem = q.get("stem") or q.get("题干") or q.get("question") or ""
        opts = q.get("options") or q.get("选项") or {}
        ans = q.get("answer") or q.get("正确答案") or q.get("correct") or ""
        exp = q.get("explain") or q.get("解析") or q.get("explanation") or ""
        print(f"【变形题 {i}】{stem}")
        if isinstance(opts, dict):
            for k in ("A", "B", "C", "D"):
                if opts.get(k):
                    print(f"   {k}. {opts[k]}")
        elif isinstance(opts, list):
            for o in opts:
                print(f"   {o}")
        print(f"   正确答案：{ans}")
        print(f"   解析：{exp}\n")


if __name__ == "__main__":
    main()
