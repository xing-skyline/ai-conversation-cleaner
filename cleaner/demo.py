"""Small deterministic store for tests and the --demo user walkthrough."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

IDS = [f"00000000-0000-4000-8000-{n:012d}" for n in range(1, 7)]


def create_demo(home: Path):
    (home / "sqlite").mkdir(parents=True)
    (home / "sessions").mkdir()
    (home / "archived_sessions").mkdir()
    titles = ["示例：编写一个计算器", "示例：空白任务", "示例：已归档的文档整理", "示例：缺失文件的残留记录"]
    with closing(sqlite3.connect(home / "sqlite/codex-dev.db")) as con:
        con.executescript("""
            CREATE TABLE local_thread_catalog_hosts(host_id TEXT PRIMARY KEY, host_kind TEXT);
            CREATE TABLE local_thread_catalog_metadata(id INTEGER PRIMARY KEY, catalog_revision INTEGER);
            CREATE TABLE local_thread_catalog(host_id TEXT, thread_id TEXT, display_title TEXT,
              source_updated_at REAL, cwd TEXT, PRIMARY KEY(host_id,thread_id));
            CREATE TABLE thread_timeline_ledger(host_id TEXT, thread_id TEXT, payload_json TEXT);
            CREATE TABLE local_thread_catalog_scan_entries(host_id TEXT, thread_id TEXT, removed INTEGER);
            INSERT INTO local_thread_catalog_hosts VALUES('local','local'),('chatgpt:test','chatgpt'),('remote:test','ssh');
            INSERT INTO local_thread_catalog_metadata VALUES(1,1);
        """)
        for n, title in enumerate(titles):
            con.execute("INSERT INTO local_thread_catalog VALUES(?,?,?,?,?)", ("local", IDS[n], title, 1790000100-n*3600, "D:\\示例项目"))
            con.execute("INSERT INTO thread_timeline_ledger VALUES(?,?,?)", ("local", IDS[n], "{}"))
        for host in ("chatgpt:test", "remote:test"):
            con.execute("INSERT INTO local_thread_catalog VALUES(?,?,?,?,?)", (host, IDS[0], "远程对话 · 保留", 1790000000, ""))
            con.execute("INSERT INTO thread_timeline_ledger VALUES(?,?,?)", (host, IDS[0], "{}"))
        con.commit()
    with closing(sqlite3.connect(home / "state_5.sqlite")) as con:
        con.executescript("""
            CREATE TABLE threads(id TEXT PRIMARY KEY, title TEXT, cwd TEXT, updated_at INTEGER,
              archived INTEGER, rollout_path TEXT, first_user_message TEXT);
            CREATE TABLE thread_dynamic_tools(thread_id TEXT, name TEXT);
            CREATE TABLE thread_attachments(thread_id TEXT, payload TEXT);
            CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT);
        """)
        for n, title in enumerate(titles[:3]):
            folder = "archived_sessions" if n == 2 else "sessions"
            rollout = home / folder / f"rollout-2026-09-22-{IDS[n]}.jsonl"
            records = [{"type": "session_meta", "payload": {"id": IDS[n], "cwd": "D:\\示例项目"}},
                       {"type": "event_msg", "payload": {"type": "user_message", "message": title}}]
            rollout.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records)+"\n", encoding="utf-8")
            con.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?)", (IDS[n], title, "D:\\示例项目", 1790000100-n*3600, n==2, str(rollout), "请帮我完成这个任务。" if n != 1 else ""))
            con.execute("INSERT INTO thread_dynamic_tools VALUES(?,?)", (IDS[n], "example_tool"))
        con.commit()
    with closing(sqlite3.connect(home / "thread_history_1.sqlite")) as con:
        con.executescript("""
            CREATE TABLE thread_turns(thread_id TEXT, turn_id TEXT);
            CREATE TABLE thread_items(thread_id TEXT, rollout_ordinal INTEGER, item_json TEXT);
            CREATE TABLE thread_history_projection_state(thread_id TEXT, next_rollout_ordinal INTEGER);
        """)
        for n in (0, 2):
            con.execute("INSERT INTO thread_items VALUES(?,?,?)", (IDS[n], 1, json.dumps({"type":"userMessage","content":[{"type":"text","text":"请帮我完成这个任务。"}]}, ensure_ascii=False)))
            con.execute("INSERT INTO thread_items VALUES(?,?,?)", (IDS[n], 2, json.dumps({"type":"agentMessage","text":"已完成。这是一条用来验证保留内容的示例消息。"}, ensure_ascii=False)))
            con.execute("INSERT INTO thread_turns VALUES(?,?)", (IDS[n], "turn-1"))
        con.commit()
    (home / "session_index.jsonl").write_text("".join(json.dumps({"id":IDS[n],"thread_name":title},ensure_ascii=False)+"\n" for n,title in enumerate(titles)), encoding="utf-8")
    (home / ".codex-global-state.json").write_text(json.dumps({"thread-titles":{"titles":dict(zip(IDS,titles)),"order":IDS[:4]}, "pinned-thread-ids":IDS[:1],"unrelated_setting":"keep me"},ensure_ascii=False),encoding="utf-8")
