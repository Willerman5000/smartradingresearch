from __future__ import annotations
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional
import requests


class RestDB:
    """Tiny Supabase/PostgREST client; avoids supabase-py RAM overhead."""

    def __init__(self, url: str, key: str, *, timeout: int = 30):
        self.url = str(url or "").rstrip("/")
        self.key = str(key or "")
        self.timeout = timeout
        self.session = requests.Session()
        base_headers = {
            "apikey": self.key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Legacy service_role keys are JWTs and can be used as Bearer tokens.
        # Modern sb_secret_* keys are API keys and must not be treated as JWTs.
        if self.key.count(".") == 2:
            base_headers["Authorization"] = f"Bearer {self.key}"
        self.session.headers.update(base_headers)

    @property
    def ready(self) -> bool:
        return bool(self.url and self.key)

    def _endpoint(self, table: str) -> str:
        return f"{self.url}/rest/v1/{table}"

    def select(self, table: str, *, params: Optional[Dict[str, str]] = None,
               headers: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        if not self.ready:
            raise RuntimeError("Supabase no configurado")
        r = self.session.get(self._endpoint(table), params=params or {}, headers=headers or {}, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def upsert(self, table: str, rows: Iterable[Dict[str, Any]], *, on_conflict: str = "") -> None:
        payload = list(rows)
        if not payload:
            return
        headers = {"Prefer": "resolution=merge-duplicates,return=minimal"}
        params = {"on_conflict": on_conflict} if on_conflict else {}
        r = self.session.post(self._endpoint(table), params=params, headers=headers, json=payload, timeout=self.timeout)
        r.raise_for_status()

    def insert(self, table: str, rows: Iterable[Dict[str, Any]]) -> None:
        payload = list(rows)
        if not payload:
            return
        headers = {"Prefer": "return=minimal"}
        r = self.session.post(self._endpoint(table), headers=headers, json=payload, timeout=self.timeout)
        r.raise_for_status()

    def patch(self, table: str, values: Dict[str, Any], *, filters: Dict[str, str]) -> None:
        r = self.session.patch(self._endpoint(table), params=filters, headers={"Prefer":"return=minimal"}, json=values, timeout=self.timeout)
        r.raise_for_status()

    def rpc(self, function: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        r = self.session.post(f"{self.url}/rest/v1/rpc/{function}", json=payload or {}, timeout=self.timeout)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return None

    def paged_select(self, table: str, *, params: Dict[str, str], max_rows: int, page_size: int) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        start = 0
        while len(out) < max_rows:
            end = min(start + page_size - 1, max_rows - 1)
            headers = {"Range": f"{start}-{end}"}
            rows = self.select(table, params=params, headers=headers)
            if not rows:
                break
            out.extend(rows)
            if len(rows) < (end - start + 1):
                break
            start = end + 1
            time.sleep(0.03)
        return out[:max_rows]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
