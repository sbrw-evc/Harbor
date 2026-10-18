"""ClickHouse stand-in for tests: the embedded engine (chdb) behind the ClickHouseClient interface."""
import json
import re

from chdb import session as chs

# chdb runs one embedded server per process, on the path of its first session: every test shares it, each with its
# own database.
_SESSION = None


class ChdbClient:
    def __init__(self, path: str, database: str = "flows"):
        global _SESSION
        if _SESSION is None:
            _SESSION = chs.Session(path)
        self.s = _SESSION
        self.database = database
        self.user = "default"
        self.password = ""
        self.log: list[str] = []

    @staticmethod
    def _bind(sql: str, params: dict | None) -> str:
        def rep(m):
            name = m.group(1)
            v = (params or {})[name]
            if isinstance(v, (int, float)):
                return str(v)
            return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"
        return re.sub(r"\{(\w+):(\w+)\}", rep, sql)

    def _run(self, sql: str, fmt: str = "CSV"):
        self.log.append(sql)
        return self.s.query(sql, fmt)

    async def command(self, sql: str, database: str | None = None) -> None:
        self._run(self._use(sql, database))

    def _use(self, sql: str, database: str | None):
        db = database if database is not None else self.database
        if db != "default":
            try:
                self.s.query(f"USE {db}")
            except Exception:
                pass
        return sql

    async def query(self, sql: str, params: dict | None = None) -> list[dict]:
        r = self._run(self._use(self._bind(sql, params), None), "JSON")
        text = r.bytes().decode() if hasattr(r, "bytes") else str(r)
        return json.loads(text)["data"] if text.strip() else []

    async def insert(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        self._run(self._use(f"INSERT INTO {self.database}.{table} FORMAT JSONEachRow {body}", None))

    async def ping(self) -> bool:
        return True
