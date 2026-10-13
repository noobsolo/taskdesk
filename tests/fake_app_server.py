"""Offline JSON-RPC fixture for end-to-end tests; production never selects it."""
import json
import sys

threads = {}
serial = 0


def write(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    msg = json.loads(line)
    method, p = msg.get("method"), msg.get("params", {})
    if not method or "id" not in msg:
        continue
    serial += 1
    result = {}
    if method == "initialize":
        result = {"userAgent": "taskdeck-offline-fixture"}
    elif method == "thread/start":
        thread = {"id": f"fixture-thread-{serial}", "status": {"type": "idle"}}
        threads[thread["id"]] = thread
        result = {"thread": thread}
    elif method == "thread/resume":
        result = {"thread": {"id": p["threadId"], "status": {"type": "idle"}}}
    elif method == "thread/turns/list":
        result = {"data": [], "nextCursor": None}
    elif method == "turn/start":
        turn = {"id": f"fixture-turn-{serial}", "status": "inProgress"}
        result = {"turn": turn}
        write({"method": "turn/started", "params": {"threadId": p["threadId"], "turn": turn}})
        write({"method": "item/started", "params": {"threadId": p["threadId"], "item": {
            "id": turn["id"] + "-user", "type": "userMessage", "content": p["input"]}}})
        write({"method": "item/agentMessage/delta", "params": {"threadId": p["threadId"], "itemId": turn["id"] + "-agent", "delta": "离线协议测试：等待手动调整。"}})
    elif method == "turn/steer":
        result = {"turnId": p["expectedTurnId"]}
        write({"method": "item/agentMessage/delta", "params": {"threadId": p["threadId"], "itemId": p["expectedTurnId"] + "-agent", "delta": "\n收到调整：" + p["input"][0]["text"]}})
    elif method == "turn/interrupt":
        write({"method": "turn/completed", "params": {"threadId": p["threadId"], "turn": {"id": p["turnId"], "status": "interrupted"}}})
    write({"id": msg["id"], "result": result})
