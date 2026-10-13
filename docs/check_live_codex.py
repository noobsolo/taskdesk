"""Read-only live protocol smoke: creates an empty session, starts no model turn."""
import asyncio
import json
from pathlib import Path
import sys

from taskdeck.codex import Codex


async def main():
    cwd = Path(sys.argv[1]).resolve()
    events = []
    async def event(message):
        events.append(message["method"])
    with (cwd / "live-codex.log").open("ab") as stderr:
        codex = Codex(["codex", "app-server"], cwd, event, stderr)
        thread_id = None
        try:
            await codex.connect()
            thread = await codex.rpc("thread/start", {"cwd": str(cwd), "sandbox": "read-only"})
            thread_id = thread["thread"]["id"]
            await codex.rpc("thread/name/set", {"threadId": thread_id, "name": "Taskdeck protocol smoke (no model turn)"})
            await codex.rpc("thread/read", {"threadId": thread_id, "includeTurns": True})
            await codex.rpc("thread/resume", {"threadId": thread_id, "cwd": str(cwd), "excludeTurns": True})
            page = await codex.rpc("thread/turns/list", {"threadId": thread_id, "limit": 100, "sortDirection": "asc", "itemsView": "full"})
            print(json.dumps({"initialize": "passed", "thread_start": "passed", "thread_read": "passed", "thread_resume": "passed", "turns_list": "passed", "model_turns_started": 0, "turn_count": len(page.get("data", []))}))
        finally:
            if thread_id:
                await codex.rpc("thread/archive", {"threadId": thread_id})
            await codex.close()


asyncio.run(main())
