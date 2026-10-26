#!/usr/bin/env python3
"""Export the conversation from a Claude Code transcript, and nothing else: what the user typed, what the assistant
wrote, every tool call and every tool result, with timestamps. Everything else (account details, API request
blobs, environment, snapshots) is dropped. Used for the screen-recorded sessions and the headless runs.

  python harness/export_session.py <transcript.jsonl> <out.jsonl>"""
import json
import sys


def blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out = []
    for c in content or []:
        t = c.get("type")
        if t == "text":
            out.append({"type": "text", "text": c.get("text", "")})
        elif t == "tool_use":
            out.append({"type": "tool_use", "id": c.get("id"), "name": c.get("name"), "input": c.get("input")})
        elif t == "tool_result":
            r = c.get("content")
            r = "\n".join(x.get("text", "") for x in r if isinstance(x, dict)) if isinstance(r, list) else str(r or "")
            out.append({"type": "tool_result", "tool_use_id": c.get("tool_use_id"), "is_error": bool(c.get("is_error")), "content": r})
    return out


def export(src, dst):
    n = 0
    with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8") as o:
        for line in f:
            e = json.loads(line)
            if "turn" in e and "user" in e:                                   # converse.py's own turn marker
                o.write(json.dumps({"type": "turn", "turn": e["turn"], "user": e["user"]}) + "\n"); n += 1; continue
            if e.get("type") == "system" and e.get("subtype") == "init":     # headless: keep what matters
                o.write(json.dumps({"type": "init", **{k: e.get(k) for k in ("model", "permissionMode", "claude_code_version", "tools")}}) + "\n"); n += 1; continue
            if e.get("type") == "result":                                     # headless: the final answer + denials
                o.write(json.dumps({"type": "result", **{k: e.get(k) for k in ("result", "permission_denials", "num_turns", "is_error")}}) + "\n"); n += 1; continue
            m = e.get("message")
            if e.get("type") not in ("user", "assistant") or not isinstance(m, dict):
                continue
            b = blocks(m.get("content"))
            if b:
                o.write(json.dumps({"type": e["type"], "timestamp": e.get("timestamp"), "content": b}) + "\n"); n += 1
    return n


if __name__ == "__main__":
    print(export(sys.argv[1], sys.argv[2]), "events")
