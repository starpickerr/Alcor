"""Serve the library independently of calendar, reminders and the organizer write queue."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from business.knowledge import Knowledge


def main():
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    library = Knowledge(root / "knowledge.sqlite3")
    try:
        for line in sys.stdin:
            request = json.loads(line)
            try:
                args = request["input"]
                if request["action"] != "invoke" or args["domain"] != "knowledge":
                    raise ValueError("不支持的知识库操作。")
                value = library.invoke(args["method"], args.get("arguments", {}), args.get("request_id"))
                reply = {"ok": True, "value": value}
            except Exception as error:
                reply = {
                    "ok": False,
                    "errorType": type(error).__name__,
                    "error": str(error) if isinstance(error, ValueError) else "知识库操作未完成，请重试。",
                }
            print(json.dumps({"id": request["id"], **reply}, ensure_ascii=False), flush=True)
    finally:
        library.close()


if __name__ == "__main__":
    main()
