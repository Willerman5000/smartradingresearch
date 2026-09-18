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
        self._provider_restricted_until = 0.0
        self._provider_restricted_reason = ''
        try:
            _os = __import__('os')
            self._provider_restricted_cooldown = max(900, int(_os.getenv('SUPABASE_402_COOLDOWN_SECONDS','21600') or 21600))
        except Exception:
            self._provider_restricted_cooldown = 21600
        # RC9.1: tiny critical reads (watermark/health) may probe recovery while
        # the normal circuit is open, but at most once every 5s per process.
        self._critical_probe_lock = threading.Lock()
        self._last_critical_probe = 0.0

        # RC8 FREE-PLAN: presupuesto diario de egress por proceso Research.
        # El plan Free de Supabase comparte 5 GB/mes de egress no cacheado en
        # toda la organización. Cada uno de los cinco motores tiene un límite
        # conservador para que un bug o una consulta demasiado grande no vuelva
        # a consumir la cuota completa.
        try:
            _os = __import__('os')
            daily_mb = float(_os.getenv('RESEARCH_EGRESS_DAILY_MB', '2') or 2)
            free_plan_mode = str(_os.getenv('RESEARCH_FREE_PLAN_MODE', '1') or '1').strip().lower() not in {'0','false','no','off'}
        except Exception:
            daily_mb = 2.0
            free_plan_mode = True
        # RC9.7.4: evidence engines stay at 2 MB/day. Validation receives a
        # still-conservative 4 MB/day because it must read the compact outputs
        # of the other four services before classifying them. This remains far
        # below the 5 GB/month Free allowance and prevents an egress guard from
        # making Validation look permanently empty.
        if free_plan_mode:
            engine_name = str(_os.getenv('RESEARCH_ENGINE', '') or '').strip().lower()
            free_cap_mb = 4.0 if engine_name == 'validation' else 2.0
            daily_mb = min(daily_mb, free_cap_mb)
        self._egress_daily_limit_bytes = max(1.0, min(64.0, daily_mb)) * 1024 * 1024
        self._egress_lock = threading.Lock()
        self._egress_day = datetime.now(timezone.utc).date().isoformat()
        self._egress_bytes_today = 0
        self._egress_critical_reserve_bytes = 2 * 1024 * 1024

    @property
    def ready(self) -> bool:
        return bool(self.url and self.key)

    def _endpoint(self, table: str) -> str:
        return f"{self.url}/rest/v1/{table}"

    @staticmethod
    def _transient_status(status: int) -> bool:
        return int(status or 0) in {500, 502, 503, 504, 520, 521, 522, 523, 524}

    def _provider_restricted(self) -> bool:
        with self._circuit_lock:
            return time.monotonic() < self._provider_restricted_until

    def _mark_provider_restricted(self, reason: str = 'HTTP_402') -> None:
        with self._circuit_lock:
            self._provider_restricted_until = max(
                self._provider_restricted_until,
                time.monotonic() + float(self._provider_restricted_cooldown),
            )
            self._provider_restricted_reason = str(reason or 'HTTP_402')[:160]
            self._read_circuit_until = max(self._read_circuit_until, self._provider_restricted_until)

    def _check_response_restriction(self, response) -> None:
        if int(getattr(response, 'status_code', 0) or 0) == 402:
            self._mark_provider_restricted('SUPABASE_402_FAIR_USE')
            raise requests.HTTPError('Supabase HTTP 402 Fair Use restriction', response=response)

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

    def _roll_egress_day(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._egress_lock:
            if self._egress_day != today:
                self._egress_day = today
                self._egress_bytes_today = 0
        self._egress_critical_reserve_bytes = 2 * 1024 * 1024

    def _record_egress(self, response) -> None:
        self._roll_egress_day()
        try:
            size = len(response.content or b"")
        except Exception:
            try:
                size = int(response.headers.get("content-length") or 0)
            except Exception:
                size = 0
        with self._egress_lock:
            self._egress_bytes_today += max(0, int(size or 0))

    def egress_stats(self) -> Dict[str, Any]:
        self._roll_egress_day()
        with self._egress_lock:
            used = int(self._egress_bytes_today)
            limit = int(self._egress_daily_limit_bytes)
            day = self._egress_day
        return {
            "day_utc": day,
            "bytes": used,
            "mb": round(used / (1024 * 1024), 3),
            "limit_mb": round(limit / (1024 * 1024), 1),
            "ratio": round((used / limit), 4) if limit > 0 else 0.0,
            "guard_open": bool(limit > 0 and used >= limit),
        }

    def egress_guard_open(self) -> bool:
        return bool(self.egress_stats().get("guard_open"))

    def select(self, table: str, *, params: Optional[Dict[str, str]] = None,
               headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None,
               retries: int = 1, priority: str = "normal") -> List[Dict[str, Any]]:
        if not self.ready:
            raise RuntimeError("Supabase no configurado")
        if self._provider_restricted():
            raise RuntimeError("SUPABASE_402_RESTRICTED")
        priority_name = str(priority or "normal").lower()
        if self._read_circuit_open():
            if priority_name != "critical":
                raise RuntimeError("SUPABASE_READ_CIRCUIT_OPEN")
            # A critical probe is a very small watermark/health read. It allows
            # the service to recover before the 15s circuit expires without
            # reopening the floodgates for normal historical queries.
            with self._critical_probe_lock:
                now_m = time.monotonic()
                if now_m - self._last_critical_probe < 5.0:
                    raise RuntimeError("SUPABASE_READ_CIRCUIT_OPEN")
                self._last_critical_probe = now_m
            retries = 0
        if self.egress_guard_open():
            stats = self.egress_stats()
            used = int(stats.get("bytes") or 0)
            hard = int(self._egress_daily_limit_bytes + self._egress_critical_reserve_bytes)
            if priority_name != "critical" or used >= hard:
                raise RuntimeError("SUPABASE_EGRESS_GUARD_OPEN")
        last = None
        for attempt in range(max(0, int(retries)) + 1):
            try:
                r = self.session.get(
                    self._endpoint(table), params=params or {}, headers=headers or {},
                    timeout=(timeout if timeout is not None else self.timeout)
                )
                self._check_response_restriction(r)
                if self._transient_status(r.status_code):
                    raise requests.HTTPError(f"Supabase transient HTTP {r.status_code}", response=r)
                r.raise_for_status()
                ctype = str(r.headers.get("content-type") or "").lower()
                if "json" not in ctype:
                    raise ValueError(f"Supabase devolvió contenido no JSON ({ctype or 'sin content-type'})")
                self._record_egress(r)
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
        if self._provider_restricted():
            raise RuntimeError("SUPABASE_402_RESTRICTED")
        if self._read_circuit_open():
            raise RuntimeError("SUPABASE_CIRCUIT_OPEN")
        headers = {"Prefer": "resolution=merge-duplicates,return=minimal"}
        params = {"on_conflict": on_conflict} if on_conflict else {}
        try:
            r = self.session.post(self._endpoint(table), params=params, headers=headers, json=payload, timeout=self.timeout)
            self._check_response_restriction(r)
            if self._transient_status(r.status_code):
                raise requests.HTTPError(f"Supabase transient HTTP {r.status_code}", response=r)
            r.raise_for_status(); self._read_success()
        except requests.RequestException:
            self._read_failure(); raise

    def insert(self, table: str, rows: Iterable[Dict[str, Any]]) -> None:
        payload = list(rows)
        if not payload:
            return
        if self._provider_restricted():
            raise RuntimeError("SUPABASE_402_RESTRICTED")
        if self._read_circuit_open():
            raise RuntimeError("SUPABASE_CIRCUIT_OPEN")
        headers = {"Prefer": "return=minimal"}
        try:
            r = self.session.post(self._endpoint(table), headers=headers, json=payload, timeout=self.timeout)
            self._check_response_restriction(r)
            if self._transient_status(r.status_code):
                raise requests.HTTPError(f"Supabase transient HTTP {r.status_code}", response=r)
            r.raise_for_status(); self._read_success()
        except requests.RequestException:
            self._read_failure(); raise

    def patch(self, table: str, values: Dict[str, Any], *, filters: Dict[str, str]) -> None:
        if self._provider_restricted():
            raise RuntimeError("SUPABASE_402_RESTRICTED")
        if self._read_circuit_open():
            raise RuntimeError("SUPABASE_CIRCUIT_OPEN")
        try:
            r = self.session.patch(self._endpoint(table), params=filters, headers={"Prefer":"return=minimal"}, json=values, timeout=self.timeout)
            self._check_response_restriction(r)
            if self._transient_status(r.status_code):
                raise requests.HTTPError(f"Supabase transient HTTP {r.status_code}", response=r)
            r.raise_for_status(); self._read_success()
        except requests.RequestException:
            self._read_failure(); raise

    def rpc(self, function: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        if self._provider_restricted():
            raise RuntimeError("SUPABASE_402_RESTRICTED")
        if self._read_circuit_open():
            raise RuntimeError("SUPABASE_CIRCUIT_OPEN")
        try:
            r = self.session.post(f"{self.url}/rest/v1/rpc/{function}", json=payload or {}, timeout=self.timeout)
            self._check_response_restriction(r)
            if self._transient_status(r.status_code):
                raise requests.HTTPError(f"Supabase transient HTTP {r.status_code}", response=r)
            r.raise_for_status(); self._read_success()
        except requests.RequestException:
            self._read_failure(); raise
        try:
            return r.json()
        except Exception:
            return None

    def paged_select(self, table: str, *, params: Dict[str, str], max_rows: int, page_size: int, priority: str = "normal") -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        start = 0
        while len(out) < max_rows:
            end = min(start + page_size - 1, max_rows - 1)
            headers = {"Range": f"{start}-{end}"}
            rows = self.select(table, params=params, headers=headers, priority=priority)
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
