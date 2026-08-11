"""End-to-end test of the AI tutor chat flow through the preview proxy (5173)."""
import sys, json
sys.path.insert(0, r"D:\制作网页\cuotixiaolaoshi\backend")
import httpx

BASE = "http://127.0.0.1:5173"

def main():
    with httpx.Client(timeout=120.0) as c:
        # 1) Login (dev 万能码)
        r = c.post(f"{BASE}/api/auth/login",
                   json={"phone": "13800000099", "code": "000000"})
        print("LOGIN", r.status_code, r.text[:200])
        tok = r.json()["data"]["access"]
        hdr = {"Authorization": f"Bearer {tok}"}

        # 2) Start chat (拍题开场)
        r = c.post(f"{BASE}/api/chat",
                   headers=hdr,
                   json={"text": "小明有12个苹果，给了小红3个，又买了5个，现在有几个？"})
        print("CHAT-START", r.status_code)
        body = r.json()
        if r.status_code != 200 or body.get("code") != 0:
            print("  FAILED:", r.text[:500])
            return
        data = body["data"]
        print("  session_id:", data.get("session_id"))
        print("  analysis:", json.dumps(data.get("analysis"), ensure_ascii=False)[:200])
        print("  first_question:", data.get("first_question"))
        assert data.get("first_question") and len(data["first_question"]) > 4

        # 3) Follow-up message (多轮引导)
        sid = data["session_id"]
        r = c.post(f"{BASE}/api/chat/{sid}/message",
                   headers=hdr,
                   json={"text": "我先算了12减3等于9，然后9加5等于14"})
        print("CHAT-MSG", r.status_code)
        body = r.json()
        if r.status_code != 200 or body.get("code") != 0:
            print("  FAILED:", r.text[:500])
            return
        print("  reply:", body["data"].get("reply"))
        assert body["data"].get("reply") and len(body["data"]["reply"]) > 4

        print("\n=== E2E PASS: /api/chat returns real AI tutor text on start AND follow-up ===")

main()
