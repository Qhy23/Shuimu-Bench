"""SQLite 存储层: 课程, 资源索引, 操作留痕.

设计见 .claude/project-proposal.md 13 节: 索引 + 引用路径, 不复制文件本体.
文件失效(被外部移动/删除)时保留记录, 由调用方标灰展示.
"""

import json
import os
import sqlite3
import time

from .config import load_config

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS resources (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('file', 'dir', 'link')),
  path_or_url TEXT NOT NULL,
  display_name TEXT,
  size INTEGER,
  mtime REAL,
  added_at REAL NOT NULL,
  UNIQUE (course_id, path_or_url)
);
CREATE TABLE IF NOT EXISTS operations (
  id INTEGER PRIMARY KEY,
  action TEXT NOT NULL,
  resource_id INTEGER REFERENCES resources(id) ON DELETE SET NULL,
  params TEXT,
  path_snapshot TEXT,
  started_at REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'failed', 'cancelled')),
  detail TEXT
);
"""


class Storage:
    """工作区数据库. 所有写操作立即提交."""

    def __init__(self, db_path: str):
        db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        # SQLite 默认不启用外键, 必须逐连接打开, 否则 ON DELETE CASCADE 不生效.
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        """关闭连接."""
        self._conn.close()

    # ---- 课程 ----

    def add_course(self, name: str) -> int:
        """新建课程, 返回课程 id."""
        cur = self._conn.execute(
            "INSERT INTO courses (name, created_at) VALUES (?, ?)", (name, time.time())
        )
        self._conn.commit()
        return cur.lastrowid

    def list_courses(self) -> list:
        """全部课程, 按创建时间排序."""
        return self._conn.execute("SELECT * FROM courses ORDER BY created_at").fetchall()

    def ensure_default_course(self, name: str = None) -> int:
        """没有任何课程时创建默认课程, 返回课程 id.

        name 缺省取配置里的 course.default_name.
        """
        if name is None:
            name = load_config()["course"]["default_name"]
        courses = self.list_courses()
        if courses:
            return courses[0]["id"]
        return self.add_course(name)

    def get_course(self, course_id: int) -> sqlite3.Row:
        """按 id 取课程."""
        return self._conn.execute(
            "SELECT * FROM courses WHERE id = ?", (course_id,)
        ).fetchone()

    def delete_course(self, course_id: int) -> int:
        """删除课程, 其资源索引经外键 CASCADE 一并移除(不动磁盘文件).

        返回被级联移除的资源条数(供留痕与提示).
        """
        count = self._conn.execute(
            "SELECT COUNT(*) AS n FROM resources WHERE course_id = ?", (course_id,)
        ).fetchone()["n"]
        self._conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
        self._conn.commit()
        return count

    # ---- 资源 ----

    def add_resource(self, course_id: int, kind: str, path_or_url: str) -> int:
        """把文件/目录/链接登记进课程. 文件类资源统一存绝对路径并记录 size+mtime."""
        if kind != "link":
            path_or_url = os.path.abspath(path_or_url)
        size, mtime = None, None
        if kind in ("file", "dir") and os.path.exists(path_or_url):
            stat = os.stat(path_or_url)
            size, mtime = stat.st_size, stat.st_mtime
        name = os.path.basename(path_or_url.rstrip("/")) if kind != "link" else path_or_url
        cur = self._conn.execute(
            "INSERT INTO resources (course_id, kind, path_or_url, display_name,"
            " size, mtime, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (course_id, kind, path_or_url, name, size, mtime, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def remove_resource(self, resource_id: int) -> None:
        """从索引移除资源(不动磁盘文件)."""
        self._conn.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
        self._conn.commit()

    def get_resource(self, resource_id: int) -> sqlite3.Row:
        """按 id 取资源."""
        return self._conn.execute(
            "SELECT * FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()

    def list_resources(self, course_id: int) -> list:
        """某课程的全部资源, 按加入时间排序."""
        return self._conn.execute(
            "SELECT * FROM resources WHERE course_id = ? ORDER BY added_at", (course_id,)
        ).fetchall()

    @staticmethod
    def check_resource(row: sqlite3.Row) -> tuple:
        """懒校验: 返回 (是否存在, 内容是否变更). 链接恒为 (True, False)."""
        if row["kind"] == "link":
            return True, False
        path = row["path_or_url"]
        if not os.path.exists(path):
            return False, False
        stat = os.stat(path)
        changed = (stat.st_size, stat.st_mtime) != (row["size"], row["mtime"])
        return True, changed

    # ---- 操作留痕 ----

    def log_operation(self, action: str, resource_id=None, params=None,
                      path_snapshot=None, status: str = "ok", detail=None) -> int:
        """记录一次操作. params/detail 传入 dict 时自动转 JSON."""
        cur = self._conn.execute(
            "INSERT INTO operations (action, resource_id, params, path_snapshot,"
            " started_at, status, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                action,
                resource_id,
                json.dumps(params, ensure_ascii=False) if isinstance(params, dict) else params,
                path_snapshot,
                time.time(),
                status,
                json.dumps(detail, ensure_ascii=False) if isinstance(detail, dict) else detail,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_operations(self, limit: int = 200) -> list:
        """最近的操作记录, 新的在前."""
        return self._conn.execute(
            "SELECT * FROM operations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
