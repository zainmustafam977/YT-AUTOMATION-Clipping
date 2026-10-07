"""
Zero-Restart Dynamic Configuration Store.
Thread-safe in-memory cache over the system_settings SQLite table.
Settings survive process restarts and propagate instantly without app restart.
"""

import threading
import json
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

class DynamicConfigStore:
    _instance: Optional['DynamicConfigStore'] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'DynamicConfigStore':
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._cache_lock = threading.Lock()

    def _deserialize_value(self, value: str, value_type: str) -> Any:
        if value_type == "int":
            return int(value)
        elif value_type == "float":
            return float(value)
        elif value_type == "bool":
            return value.lower() in ("true", "1", "yes")
        elif value_type == "json":
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return value  # default to str

    def _serialize_value(self, value: Any) -> tuple[str, str]:
        if isinstance(value, bool):
            return "true" if value else "false", "bool"
        elif isinstance(value, int):
            return str(value), "int"
        elif isinstance(value, float):
            return str(value), "float"
        elif isinstance(value, (dict, list)):
            return json.dumps(value), "json"
        return str(value), "str"

    def load_from_db(self, db: Session) -> None:
        try:
            result = db.execute(text("SELECT key, value, value_type FROM system_settings")).fetchall()
            with self._cache_lock:
                self._cache.clear()
                for row in result:
                    # Depending on sqlalchemy version, row can be tuple or RowProxy
                    key = row[0]
                    val = row[1]
                    vtype = row[2]
                    self._cache[key] = self._deserialize_value(val, vtype)
        except OperationalError:
            # Table might not exist yet
            pass

    def get(self, key: str, default: Any = None) -> Any:
        with self._cache_lock:
            return self._cache.get(key, default)

    def set(self, key: str, value: Any, db: Session) -> None:
        val_str, vtype = self._serialize_value(value)
        try:
            db.execute(text(
                "INSERT INTO system_settings (key, value, value_type) "
                "VALUES (:key, :value, :type) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, value_type=excluded.value_type"
            ), {"key": key, "value": val_str, "type": vtype})
            db.commit()
        except OperationalError:
            # Create table if not exists then try again
            db.rollback()
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS system_settings ("
                "key TEXT PRIMARY KEY, value TEXT, value_type TEXT)"
            ))
            db.execute(text(
                "INSERT INTO system_settings (key, value, value_type) "
                "VALUES (:key, :value, :type) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, value_type=excluded.value_type"
            ), {"key": key, "value": val_str, "type": vtype})
            db.commit()

        with self._cache_lock:
            self._cache[key] = value

    def refresh(self, db: Session) -> None:
        self.load_from_db(db)

    def to_dict(self) -> Dict[str, Any]:
        with self._cache_lock:
            return self._cache.copy()
