import json
import pg8000
from datetime import datetime
from config import DB_CONFIG

class Database:
    def __init__(self):
        self.conn = pg8000.connect(
            database=DB_CONFIG["NAME"],
            user=DB_CONFIG["USER"],
            password=DB_CONFIG["PASSWORD"],
            host=DB_CONFIG["HOST"],
            port=int(DB_CONFIG["PORT"]),
        )
        self.cur = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_contexts (
                chat_id BIGINT NOT NULL,
                bot_key TEXT NOT NULL,
                messages JSONB NOT NULL DEFAULT '[]'::jsonb,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (chat_id, bot_key)
            );
            """
        )
        self.cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_logs (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                bot_key TEXT,
                username TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        self.cur.execute(
            """
            CREATE TABLE IF NOT EXISTS whitelist (
                entity_id BIGINT PRIMARY KEY,
                added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        self.cur.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_stats (
                entity_id BIGINT PRIMARY KEY,
                total_points BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        self.cur.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_stats_users (
                username TEXT PRIMARY KEY,
                total_points BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        self.cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value_bool BOOLEAN,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        self.conn.commit()

    def get_context(self, chat_id, bot_key):
        self.cur.execute(
            "SELECT messages FROM chat_contexts WHERE chat_id=%s AND bot_key=%s;",
            (chat_id, bot_key),
        )
        row = self.cur.fetchone()
        if not row:
            return []
        val = row[0]
        if isinstance(val, (dict, list)):
            return val
        try:
            return json.loads(val)
        except Exception:
            return []

    def set_context(self, chat_id, bot_key, messages):
        payload = json.dumps(messages, ensure_ascii=False)
        self.cur.execute(
            """
            INSERT INTO chat_contexts (chat_id, bot_key, messages, updated_at)
            VALUES (%s, %s, %s::jsonb, NOW())
            ON CONFLICT (chat_id, bot_key)
            DO UPDATE SET messages = EXCLUDED.messages, updated_at = NOW();
            """,
            (chat_id, bot_key, payload),
        )
        self.conn.commit()

    def clear_context(self, chat_id, bot_key):
        self.cur.execute(
            "DELETE FROM chat_contexts WHERE chat_id=%s AND bot_key=%s;",
            (chat_id, bot_key),
        )
        self.conn.commit()

    def append_log(self, chat_id, bot_key, username, role, content):
        self.cur.execute(
            """
            INSERT INTO chat_logs (chat_id, bot_key, username, role, content, created_at)
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (chat_id, bot_key, username, role, content, datetime.utcnow()),
        )
        self.conn.commit()

    def is_whitelisted(self, entity_id: int) -> bool:
        self.cur.execute("SELECT 1 FROM whitelist WHERE entity_id=%s;", (entity_id,))
        return self.cur.fetchone() is not None

    def add_to_whitelist(self, entity_id: int):
        self.cur.execute(
            """
            INSERT INTO whitelist (entity_id, added_at)
            VALUES (%s, NOW())
            ON CONFLICT (entity_id) DO NOTHING;
            """,
            (entity_id,),
        )
        self.conn.commit()

    def list_whitelist(self):
        self.cur.execute("SELECT entity_id FROM whitelist ORDER BY added_at ASC;")
        rows = self.cur.fetchall()
        return [r[0] for r in rows]

    def remove_from_whitelist(self, entity_id: int):
        self.cur.execute("DELETE FROM whitelist WHERE entity_id=%s;", (entity_id,))
        self.conn.commit()

    def list_whitelist_details(self):
        self.cur.execute("SELECT entity_id, added_at FROM whitelist ORDER BY added_at ASC;")
        rows = self.cur.fetchall()
        out = []
        for entity_id, added_at in rows:
            self.cur.execute(
                "SELECT username, created_at FROM chat_logs WHERE chat_id=%s ORDER BY created_at DESC LIMIT 1;",
                (entity_id,)
            )
            r = self.cur.fetchone()
            last_username = r[0] if r else None
            last_activity = r[1] if r else None
            out.append({
                "entity_id": entity_id,
                "added_at": added_at,
                "last_username": last_username,
                "last_activity": last_activity,
            })
        return out

    def increment_usage(self, entity_id: int, points: int):
        self.cur.execute(
            """
            INSERT INTO usage_stats (entity_id, total_points, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (entity_id)
            DO UPDATE SET total_points = usage_stats.total_points + EXCLUDED.total_points, updated_at = NOW();
            """,
            (entity_id, points),
        )
        self.conn.commit()

    def list_usage_leaderboard(self):
        self.cur.execute(
            """
            SELECT us.entity_id, us.total_points,
                   (SELECT username FROM chat_logs WHERE chat_id = us.entity_id AND username IS NOT NULL AND username <> '' ORDER BY created_at DESC LIMIT 1) AS last_username
            FROM usage_stats us
            WHERE us.total_points > 0
            ORDER BY us.total_points DESC;
            """
        )
        rows = self.cur.fetchall()
        out = []
        for entity_id, total_points, last_username in rows:
            out.append({
                "entity_id": entity_id,
                "total_points": int(total_points) if total_points is not None else 0,
                "last_username": last_username,
            })
        return out

    def increment_usage_username(self, username: str, points: int):
        self.cur.execute(
            """
            INSERT INTO usage_stats_users (username, total_points, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (username)
            DO UPDATE SET total_points = usage_stats_users.total_points + EXCLUDED.total_points, updated_at = NOW();
            """,
            (username, points),
        )
        self.conn.commit()

    def list_usage_leaderboard_usernames(self):
        self.cur.execute(
            """
            SELECT username, total_points
            FROM usage_stats_users
            WHERE total_points > 0
            ORDER BY total_points DESC;
            """
        )
        rows = self.cur.fetchall()
        out = []
        for username, total_points in rows:
            out.append({
                "username": username,
                "total_points": int(total_points) if total_points is not None else 0,
            })
        return out

    def reset_usage_leaderboard_usernames(self):
        self.cur.execute("DELETE FROM usage_stats_users;")
        self.conn.commit()

    def get_economy_mode(self) -> bool:
        self.cur.execute("SELECT value_bool FROM app_settings WHERE key=%s;", ("economy_mode",))
        row = self.cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else False

    def set_economy_mode(self, value: bool):
        self.cur.execute(
            """
            INSERT INTO app_settings (key, value_bool, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key)
            DO UPDATE SET value_bool = EXCLUDED.value_bool, updated_at = NOW();
            """,
            ("economy_mode", value),
        )
        self.conn.commit()

    def get_collapsible_quote_mode(self, chat_id: int) -> bool:
        self.cur.execute("SELECT value_bool FROM app_settings WHERE key=%s;", (f"cq:{chat_id}",))
        row = self.cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else False

    def set_collapsible_quote_mode(self, chat_id: int, value: bool):
        self.cur.execute(
            """
            INSERT INTO app_settings (key, value_bool, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key)
            DO UPDATE SET value_bool = EXCLUDED.value_bool, updated_at = NOW();
            """,
            (f"cq:{chat_id}", value),
        )
        self.conn.commit()