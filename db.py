from __future__ import annotations
import json
import time
import threading
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
        # RC8 resilience: one slow Supabase origin must not pin every Render thread.
        self._circuit_lock = threading.Lock()
        self._read_circuit_until = 0.0
        self._transient_failures = 0

    @property
    def ready(self) -> bool:
        return bool(self.url and self.key)

    def _endpoint(self, table: str) -> str:
        return f"{self.url}/rest/v1/{table}"

    @staticmethod
    def _transient_status(status: int) -> bool:
        return int(status or 0) in {500, 502, 503, 504, 520, 521, 522, 523, 524}

    def _read_circuit_open(self) -> bool:
        with self._circuit_lock:
            return time.monotonic() < self._read_circuit_until

    def _read_success(self) -> None:
        with self._circuit_lock:
            self._transient_failures = 0
            self._read_circuit_until = 0.0

    def _read_failure(self) -> None:
        with self._circuit_lock:
            self._transient_failures += 1
            # Short circuit only after repeated transient failures. This prevents
            # five Research services/dashboard polls from amplifying a Supabase 522.
            if self._transient_failures >= 2:
                self._read_circuit_until = time.monotonic() + 15.0

    def select(self, table: str, *, params: Optional[Dict[str, str]] = None,
               headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None,
               retries: int = 1) -> List[Dict[str, Any]]:
        if not self.ready:
            raise RuntimeError("Supabase no configurado")
        if self._read_circuit_open():
            raise RuntimeError("SUPABASE_READ_CIRCUIT_OPEN")
        last = None
        for attempt in range(max(0, int(retries)) + 1):
            try:
                r = self.session.get(
                    self._endpoint(table), params=params or {}, headers=headers or {},
                    timeout=(timeout if timeout is not None else self.timeout)
                )
                if self._transient_status(r.status_code):
                    raise requests.HTTPError(f"Supabase transient HTTP {r.status_code}", response=r)
                r.raise_for_status()
                ctype = str(r.headers.get("content-type") or "").lower()
                if "json" not in ctype:
                    raise ValueError(f"Supabase devolvió contenido no JSON ({ctype or 'sin content-type'})")
                data = r.json()
                self._read_success()
                return data if isinstance(data, list) else []
            except (requests.RequestException, ValueError) as exc:
                last = exc
                self._read_failure()
                if attempt < int(retries):
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise
        raise last or RuntimeError("Supabase read failed")

    def upsert(self, table: str, rows: Iterable[Dict[str, Any]], *, on_conflict: str = "") -> None:
        payload = list(rows)
        if not payload:
            return
        if self._read_circuit_open():
            raise RuntimeError("SUPABASE_CIRCUIT_OPEN")
        headers = {"Prefer": "resolution=merge-duplicates,return=minimal"}
        params = {"on_conflict": on_conflict} if on_conflict else {}
        try:
            r = self.session.post(self._endpoint(table), params=params, headers=headers, json=payload, timeout=self.timeout)
            if self._transient_status(r.status_code):
                raise requests.HTTPError(f"Supabase transient HTTP {r.status_code}", response=r)
            r.raise_for_status(); self._read_success()
        except requests.RequestException:
            self._read_failure(); raise

    def insert(self, table: str, rows: Iterable[Dict[str, Any]]) -> None:
        payload = list(rows)
        if not payload:
            return
        if self._read_circuit_open():
            raise RuntimeError("SUPABASE_CIRCUIT_OPEN")
        headers = {"Prefer": "return=minimal"}
        try:
            r = self.session.post(self._endpoint(table), headers=headers, json=payload, timeout=self.timeout)
            if self._transient_status(r.status_code):
                raise requests.HTTPError(f"Supabase transient HTTP {r.status_code}", response=r)
            r.raise_for_status(); self._read_success()
        except requests.RequestException:
            self._read_failure(); raise

    def patch(self, table: str, values: Dict[str, Any], *, filters: Dict[str, str]) -> None:
        if self._read_circuit_open():
            raise RuntimeError("SUPABASE_CIRCUIT_OPEN")
        try:
            r = self.session.patch(self._endpoint(table), params=filters, headers={"Prefer":"return=minimal"}, json=values, timeout=self.timeout)
            if self._transient_status(r.status_code):
                raise requests.HTTPError(f"Supabase transient HTTP {r.status_code}", response=r)
            r.raise_for_status(); self._read_success()
        except requests.RequestException:
            self._read_failure(); raise

    def rpc(self, function: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        if self._read_circuit_open():
            raise RuntimeError("SUPABASE_CIRCUIT_OPEN")
        try:
            r = self.session.post(f"{self.url}/rest/v1/rpc/{function}", json=payload or {}, timeout=self.timeout)
            if self._transient_status(r.status_code):
                raise requests.HTTPError(f"Supabase transient HTTP {r.status_code}", response=r)
            r.raise_for_status(); self._read_success()
        except requests.RequestException:
            self._read_failure(); raise
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
