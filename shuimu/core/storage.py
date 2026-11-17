"""SQLite 存储层: 课程, 资源索引, 操作留痕.

设计见 .claude/project-proposal.md 13 节: 索引 + 引用路径, 不复制文件本体.
文件失效(被外部移动/删除)时保留记录, 由调用方标灰展示.
"""

import json
import os
import sqlite3
import time

from shuimu.core.config import load_config
from shuimu.core.learn import (
    LearnCourse,
    LearnFile,
    LearnHomework,
    LearnNotice,
)

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS folders (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
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
  folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
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
CREATE TABLE IF NOT EXISTS learn_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS learn_courses (
  wlkcid TEXT PRIMARY KEY,
  semester_id TEXT NOT NULL,
  kcm TEXT NOT NULL,
  kch TEXT,
  kxh TEXT,
  jsm TEXT,
  course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
  first_seen_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS learn_homework (
  wlkcid TEXT NOT NULL REFERENCES learn_courses(wlkcid) ON DELETE CASCADE,
  zyid TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','submitted','graded')),
  xszyid TEXT,
  bt TEXT,
  jzsj TEXT,
  bjjzsj TEXT,
  sfbj TEXT,
  scsj TEXT,
  cj TEXT,
  pynr TEXT,
  detail_html TEXT,
  todo_tid INTEGER,
  first_seen_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (wlkcid, zyid)
);
CREATE TABLE IF NOT EXISTS learn_notices (
  wlkcid TEXT NOT NULL REFERENCES learn_courses(wlkcid) ON DELETE CASCADE,
  ggid TEXT NOT NULL,
  bt TEXT,
  ggnr TEXT,
  fbsj TEXT,
  jzsj TEXT,
  sfyd TEXT,
  fjmc TEXT,
  first_seen_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (wlkcid, ggid)
);
CREATE TABLE IF NOT EXISTS learn_files (
  wlkcid TEXT NOT NULL REFERENCES learn_courses(wlkcid) ON DELETE CASCADE,
  kjxxid TEXT NOT NULL,
  wjid TEXT,
  bt TEXT,
  ms TEXT,
  scsj TEXT,
  wjdx INTEGER,
  kjflid TEXT,
  category TEXT,
  first_seen_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (wlkcid, kjxxid)
);
CREATE TABLE IF NOT EXISTS learn_downloads (
  kind TEXT NOT NULL CHECK (kind IN ('file','homework','notice')),
  remote_id TEXT NOT NULL,
  wlkcid TEXT NOT NULL,
  path TEXT NOT NULL,
  resource_id INTEGER REFERENCES resources(id) ON DELETE SET NULL,
  downloaded_at REAL NOT NULL,
  PRIMARY KEY (kind, remote_id)
);
"""


class Storage:
    """工作区数据库. 所有写操作立即提交."""

    def __init__(self, db_path: str):
        if db_path == ":memory:":
            # SQLite 内存库特殊串: abspath 会令其退化为当前目录下的
            # 真实文件 ":memory:", 须原样传给 connect 且无需建目录
            self.db_path = db_path
        else:
            db_path = os.path.abspath(db_path)
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        # SQLite 默认不启用外键, 必须逐连接打开, 否则 ON DELETE CASCADE 不生效.
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        # 旧库迁移: 2026-11-16 前的 resources 无 folder_id (SQLite 的
        # CREATE TABLE IF NOT EXISTS 不会为旧表补列), 检测后 ALTER 补上
        columns = {row[1] for row in
                   self._conn.execute("PRAGMA table_info(resources)")}
        if "folder_id" not in columns:
            self._conn.execute(
                "ALTER TABLE resources ADD COLUMN folder_id INTEGER"
                " REFERENCES folders(id) ON DELETE SET NULL")
            self._conn.commit()

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

    # ---- 虚拟文件夹 (索引组织层, 不对应磁盘目录) ----

    def add_folder(self, course_id: int, parent_id: int, name: str) -> int:
        """在 parent_id(None=课程根)下建虚拟文件夹, 同名兄弟拒绝."""
        if parent_id is not None:
            parent = self.get_folder(parent_id)
            if parent is None or parent["course_id"] != course_id:
                raise ValueError(f"父文件夹不存在或不属于该课程: #{parent_id}")
        siblings = self.list_folders(course_id)
        if any(f["name"] == name and f["parent_id"] == parent_id
               for f in siblings):
            raise ValueError(f"同名文件夹已存在: {name}")
        cur = self._conn.execute(
            "INSERT INTO folders (course_id, parent_id, name, created_at)"
            " VALUES (?, ?, ?, ?)",
            (course_id, parent_id, name, time.time()))
        self._conn.commit()
        return cur.lastrowid

    def list_folders(self, course_id: int) -> list:
        """课程的全部文件夹, 按创建时间排序."""
        return self._conn.execute(
            "SELECT * FROM folders WHERE course_id = ? ORDER BY created_at",
            (course_id,)).fetchall()

    def get_folder(self, folder_id: int):
        """按 id 取文件夹行, 无则 None."""
        return self._conn.execute(
            "SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()

    def folder_rename(self, folder_id: int, name: str) -> None:
        """文件夹改名(仅索引层)."""
        self._conn.execute(
            "UPDATE folders SET name = ? WHERE id = ?", (name, folder_id))
        self._conn.commit()

    def folder_delete(self, folder_id: int) -> None:
        """删除空文件夹; 非空抛 ValueError(子项须先移走或级联清掉)."""
        if self.count_folder_children(folder_id):
            folder = self.get_folder(folder_id)
            raise ValueError(f"文件夹非空: {folder['name']}")
        self._conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        self._conn.commit()

    def folder_descendants(self, folder_id: int) -> list:
        """自身及全部子孙文件夹 id 列表(BFS)."""
        ids, queue = [folder_id], [folder_id]
        while queue:
            current = queue.pop(0)
            for row in self._conn.execute(
                    "SELECT id FROM folders WHERE parent_id = ?", (current,)):
                ids.append(row["id"])
                queue.append(row["id"])
        return ids

    def count_folder_children(self, folder_id: int) -> int:
        """直接子文件夹数 + 直接归属资源数."""
        folders = self._conn.execute(
            "SELECT COUNT(*) FROM folders WHERE parent_id = ?",
            (folder_id,)).fetchone()[0]
        resources = self._conn.execute(
            "SELECT COUNT(*) FROM resources WHERE folder_id = ?",
            (folder_id,)).fetchone()[0]
        return folders + resources

    def folder_set_parent(self, folder_id: int, parent_id) -> None:
        """移动文件夹到另一父文件夹(None=课程根)."""
        self._conn.execute(
            "UPDATE folders SET parent_id = ? WHERE id = ?",
            (parent_id, folder_id))
        self._conn.commit()

    def folder_delete_tree(self, folder_id: int) -> None:
        """删除文件夹行, 子文件夹经外键级联(调用方应已清掉其中资源行)."""
        self._conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        self._conn.commit()

    def remove_resources_in_folders(self, folder_ids: list) -> int:
        """删除归属这些文件夹的全部索引行(磁盘不动), 返回删除行数."""
        if not folder_ids:
            return 0
        marks = ",".join("?" * len(folder_ids))
        cur = self._conn.execute(
            f"DELETE FROM resources WHERE folder_id IN ({marks})", folder_ids)
        self._conn.commit()
        return cur.rowcount

    def count_resources_in_folders(self, folder_ids: list) -> int:
        """归属这些文件夹的索引行总数(只读, 不删)."""
        if not folder_ids:
            return 0
        marks = ",".join("?" * len(folder_ids))
        return self._conn.execute(
            f"SELECT COUNT(*) FROM resources WHERE folder_id IN ({marks})",
            folder_ids).fetchone()[0]

    def set_resource_folder(self, resource_id: int, folder_id) -> None:
        """把索引条目移入文件夹(None=课程根)."""
        self._conn.execute(
            "UPDATE resources SET folder_id = ? WHERE id = ?",
            (folder_id, resource_id))
        self._conn.commit()

    # ---- 资源 ----

    def add_resource(self, course_id: int, kind: str, path_or_url: str,
                     display_name: str = None, folder_id: int = None) -> int:
        """把文件/目录/链接登记进课程. 文件类资源统一存绝对路径并记录 size+mtime.

        display_name 缺省时: 文件/目录取 basename, 链接取完整 URL; 云盘等
        场景可显式传入显示名. folder_id 为归属虚拟文件夹(None=课程根).
        """
        if kind != "link":
            path_or_url = os.path.abspath(path_or_url)
        size, mtime = None, None
        if kind in ("file", "dir") and os.path.exists(path_or_url):
            stat = os.stat(path_or_url)
            size, mtime = stat.st_size, stat.st_mtime
        if display_name is not None:
            name = display_name
        else:
            name = os.path.basename(path_or_url.rstrip("/")) \
                if kind != "link" else path_or_url
        cur = self._conn.execute(
            "INSERT INTO resources (course_id, kind, path_or_url, display_name,"
            " size, mtime, added_at, folder_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (course_id, kind, path_or_url, name, size, mtime, time.time(),
             folder_id),
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

    def find_resource(self, course_id: int, path_or_url: str):
        """按 (课程, 路径) 查已登记资源行, 无则 None(幂等登记前置检查)."""
        return self._conn.execute(
            "SELECT * FROM resources WHERE course_id = ? AND path_or_url = ?",
            (course_id, path_or_url)).fetchone()

    def resources_under(self, path: str) -> list:
        """路径本身与其子孙目录内的全部登记资源(跨课程, 按路径排序).

        供文件操作同步索引: 前缀匹配以 / 为目录边界, 不误伤同前缀名.
        """
        esc = (path.replace("\\", "\\\\")
               .replace("%", "\\%").replace("_", "\\_"))
        return self._conn.execute(
            "SELECT * FROM resources WHERE path_or_url = ?"
            " OR path_or_url LIKE ? ESCAPE '\\' ORDER BY path_or_url",
            (path, esc + "/%")).fetchall()

    def retarget_resource(self, resource_id: int, new_path: str) -> None:
        """文件被移动/重命名后同步登记路径, 顺带刷新显示名与 size/mtime."""
        size, mtime = None, None
        if os.path.exists(new_path):
            stat = os.stat(new_path)
            size, mtime = stat.st_size, stat.st_mtime
        self._conn.execute(
            "UPDATE resources SET path_or_url = ?,"
            " display_name = ?, size = ?, mtime = ? WHERE id = ?",
            (new_path, os.path.basename(new_path.rstrip("/")),
             size, mtime, resource_id))
        self._conn.commit()

    def search_resources(self, keyword: str) -> list:
        """跨课程按子串搜索资源, display_name 或 path_or_url 任一命中即匹配.

        大小写不敏感(仅 ASCII 字母); 关键词中的 % 与 _ 按字面处理
        (LIKE ... ESCAPE 转义), 失效资源照常返回. 结果行含课程名
        (course_name 列), 按加入时间与 id 排序.
        """
        esc = (keyword.replace("\\", "\\\\")
               .replace("%", "\\%").replace("_", "\\_"))
        pattern = f"%{esc}%"
        return self._conn.execute(
            "SELECT r.*, c.name AS course_name"
            " FROM resources r JOIN courses c ON c.id = r.course_id"
            " WHERE r.display_name LIKE ? ESCAPE '\\'"
            " OR r.path_or_url LIKE ? ESCAPE '\\'"
            " ORDER BY r.added_at, r.id",
            (pattern, pattern),
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

    # ---- 网络学堂缓存 (差分同步, 保留用户标注列) ----

    def learn_sync_courses(self, semester_id: str,
                           rows: list[LearnCourse]) -> dict:
        """课程列表差分入库(表内只保留最新学期的当前列表).

        已关联行保留 course_id; 返回 {"new": [(wlkcid, kcm)],
        "removed": [wlkcid]}. 消失的课程级联清理其作业/公告/课件.
        """
        now = time.time()
        existing = {row["wlkcid"]: row for row in self._conn.execute(
            "SELECT * FROM learn_courses").fetchall()}
        keep = {course.wlkcid for course in rows}
        removed = []
        for wlkcid in existing:
            # 只删"本次列表里没有的"(学期切换时旧 wlkcid 自然不在新列表,
            # 留存的行随后被 UPDATE 换上新学期 id)
            if wlkcid not in keep:
                self._conn.execute(
                    "DELETE FROM learn_courses WHERE wlkcid = ?", (wlkcid,))
                removed.append(wlkcid)
        new = []
        for course in rows:
            if course.wlkcid in existing:
                self._conn.execute(
                    "UPDATE learn_courses SET semester_id = ?, kcm = ?,"
                    " kch = ?, kxh = ?, jsm = ?, updated_at = ?"
                    " WHERE wlkcid = ?",
                    (semester_id, course.kcm, course.kch, course.kxh,
                     course.jsm, now, course.wlkcid))
                continue
            self._conn.execute(
                "INSERT INTO learn_courses (wlkcid, semester_id, kcm, kch,"
                " kxh, jsm, first_seen_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (course.wlkcid, semester_id, course.kcm, course.kch,
                 course.kxh, course.jsm, now, now))
            new.append((course.wlkcid, course.kcm))
        self._conn.commit()
        return {"new": new, "removed": removed}

    def learn_set_course_link(self, wlkcid: str, course_id) -> None:
        """设置/清除学堂课程与本地课程的关联(手动或自动匹配)."""
        self._conn.execute(
            "UPDATE learn_courses SET course_id = ?, updated_at = ?"
            " WHERE wlkcid = ?",
            (course_id, time.time(), wlkcid))
        self._conn.commit()

    def learn_sync_homework(self, rows: list[LearnHomework]) -> dict:
        """作业差分入库(保留 detail_html 与 todo_tid).

        返回 {"new": [(wlkcid, zyid, bt)],
        "changed": [(wlkcid, zyid, bt, 旧state, 新state)]}.
        """
        now = time.time()
        new, changed = [], []
        for hw in rows:
            row = self._conn.execute(
                "SELECT state FROM learn_homework"
                " WHERE wlkcid = ? AND zyid = ?",
                (hw.wlkcid, hw.zyid)).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO learn_homework (wlkcid, zyid, state,"
                    " xszyid, bt, jzsj, bjjzsj, sfbj, scsj, cj, pynr,"
                    " first_seen_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (hw.wlkcid, hw.zyid, hw.state, hw.xszyid, hw.bt,
                     hw.jzsj, hw.bjjzsj, "1" if hw.sfbj else "0",
                     hw.scsj, hw.cj, hw.pynr, now, now))
                new.append((hw.wlkcid, hw.zyid, hw.bt))
                continue
            self._conn.execute(
                "UPDATE learn_homework SET state = ?, xszyid = ?, bt = ?,"
                " jzsj = ?, bjjzsj = ?, sfbj = ?, scsj = ?, cj = ?,"
                " pynr = ?, updated_at = ? WHERE wlkcid = ? AND zyid = ?",
                (hw.state, hw.xszyid, hw.bt, hw.jzsj, hw.bjjzsj,
                 "1" if hw.sfbj else "0", hw.scsj, hw.cj, hw.pynr, now,
                 hw.wlkcid, hw.zyid))
            if row["state"] != hw.state:
                changed.append((hw.wlkcid, hw.zyid, hw.bt,
                                row["state"], hw.state))
        self._conn.commit()
        return {"new": new, "changed": changed}

    def learn_save_homework_detail(self, wlkcid: str, zyid: str,
                                   detail_html: str) -> None:
        """缓存作业描述富文本."""
        self._conn.execute(
            "UPDATE learn_homework SET detail_html = ?, updated_at = ?"
            " WHERE wlkcid = ? AND zyid = ?",
            (detail_html, time.time(), wlkcid, zyid))
        self._conn.commit()

    def learn_set_homework_todo(self, wlkcid: str, zyid: str,
                                tid) -> None:
        """记录作业与日程待办 id 的映射."""
        self._conn.execute(
            "UPDATE learn_homework SET todo_tid = ?, updated_at = ?"
            " WHERE wlkcid = ? AND zyid = ?",
            (tid, time.time(), wlkcid, zyid))
        self._conn.commit()

    def learn_sync_notices(self, rows: list[LearnNotice]) -> dict:
        """公告差分入库; 返回 {"new": [(wlkcid, ggid, bt)]}."""
        now = time.time()
        new = []
        for notice in rows:
            row = self._conn.execute(
                "SELECT ggid FROM learn_notices"
                " WHERE wlkcid = ? AND ggid = ?",
                (notice.wlkcid, notice.ggid)).fetchone()
            if row is None:
                new.append((notice.wlkcid, notice.ggid, notice.bt))
            self._conn.execute(
                "INSERT INTO learn_notices (wlkcid, ggid, bt, ggnr, fbsj,"
                " jzsj, sfyd, fjmc, first_seen_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(wlkcid, ggid) DO UPDATE SET"
                " bt = excluded.bt,"
                " ggnr = CASE WHEN excluded.ggnr = ''"
                "  THEN learn_notices.ggnr ELSE excluded.ggnr END,"
                " fbsj = excluded.fbsj, jzsj = excluded.jzsj,"
                " sfyd = excluded.sfyd, fjmc = excluded.fjmc,"
                " updated_at = excluded.updated_at",
                (notice.wlkcid, notice.ggid, notice.bt, notice.body_html,
                 notice.fbsj, notice.jzsj, "1" if notice.sfyd else "0",
                 notice.fjmc, now, now))
        self._conn.commit()
        return {"new": new}

    def learn_sync_files(self, rows: list[LearnFile],
                         categories: dict = None) -> dict:
        """课件差分入库(分类 id 回填名称); 返回 {"new": [(wlkcid, kjxxid, bt)]}."""
        categories = categories or {}
        now = time.time()
        new = []
        for item in rows:
            row = self._conn.execute(
                "SELECT kjxxid FROM learn_files"
                " WHERE wlkcid = ? AND kjxxid = ?",
                (item.wlkcid, item.kjxxid)).fetchone()
            if row is None:
                new.append((item.wlkcid, item.kjxxid, item.bt))
            self._conn.execute(
                "INSERT INTO learn_files (wlkcid, kjxxid, wjid, bt, ms,"
                " scsj, wjdx, kjflid, category, first_seen_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(wlkcid, kjxxid) DO UPDATE SET"
                " wjid = excluded.wjid, bt = excluded.bt, ms = excluded.ms,"
                " scsj = excluded.scsj, wjdx = excluded.wjdx,"
                " kjflid = excluded.kjflid, category = excluded.category,"
                " updated_at = excluded.updated_at",
                (item.wlkcid, item.kjxxid, item.wjid, item.bt, item.ms,
                 item.scsj, item.wjdx, item.kjflid,
                 categories.get(item.kjflid, ""), now, now))
        self._conn.commit()
        return {"new": new}

    def learn_list_courses(self) -> list:
        """缓存的学堂课程(按课名排序)."""
        return self._conn.execute(
            "SELECT * FROM learn_courses ORDER BY kcm").fetchall()

    def learn_list_homework(self, wlkcid: str = None,
                            state: str = None) -> list:
        """缓存的作业(可按课/状态过滤, 按标题排序)."""
        query = "SELECT * FROM learn_homework"
        conditions, args = [], []
        if wlkcid is not None:
            conditions.append("wlkcid = ?")
            args.append(wlkcid)
        if state is not None:
            conditions.append("state = ?")
            args.append(state)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        return self._conn.execute(
            query + " ORDER BY bt", args).fetchall()

    def learn_list_notices(self, wlkcid: str) -> list:
        """某课缓存的公告(按发布时间倒序)."""
        return self._conn.execute(
            "SELECT * FROM learn_notices WHERE wlkcid = ?"
            " ORDER BY fbsj DESC, ggid", (wlkcid,)).fetchall()

    def learn_list_files(self, wlkcid: str) -> list:
        """某课缓存的课件(按标题排序)."""
        return self._conn.execute(
            "SELECT * FROM learn_files WHERE wlkcid = ?"
            " ORDER BY bt, kjxxid", (wlkcid,)).fetchall()

    def learn_find_file(self, wjid: str):
        """按文件 id 找课件行(下载入口用)."""
        return self._conn.execute(
            "SELECT * FROM learn_files WHERE wjid = ?", (wjid,)).fetchone()

    def learn_record_download(self, kind: str, remote_id: str,
                              wlkcid: str, path: str, resource_id=None) -> None:
        """记录一次下载(路径与登记的资源 id)."""
        self._conn.execute(
            "INSERT INTO learn_downloads (kind, remote_id, wlkcid, path,"
            " resource_id, downloaded_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(kind, remote_id) DO UPDATE SET"
            " path = excluded.path, resource_id = excluded.resource_id,"
            " downloaded_at = excluded.downloaded_at",
            (kind, remote_id, wlkcid, path, resource_id, time.time()))
        self._conn.commit()

    def learn_find_download(self, kind: str, remote_id: str):
        """查已下载记录; 无则 None."""
        return self._conn.execute(
            "SELECT * FROM learn_downloads"
            " WHERE kind = ? AND remote_id = ?",
            (kind, remote_id)).fetchone()

    def learn_meta_get(self, key: str):
        """学堂元信息(学期 id/刷新时间等); 无则 None."""
        row = self._conn.execute(
            "SELECT value FROM learn_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def learn_meta_set(self, key: str, value: str) -> None:
        """写学堂元信息."""
        self._conn.execute(
            "INSERT INTO learn_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)))
        self._conn.commit()
