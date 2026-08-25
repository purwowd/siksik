from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse, urlsplit

import httpx

CHROME_EPOCH_OFFSET_SECONDS = 11_644_473_600
CHROME_PACKAGE_ACTIVITY = (
    "com.android.chrome/org.chromium.chrome.browser.ChromeTabbedActivity"
)
DEVTOOLS_SOCKET = "chrome_devtools_remote"

_SEARCH_HOSTS_Q = ("google.", "bing.com", "duckduckgo.com")


def chrome_time_to_utc(value: Any) -> str | None:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    unix_sec = (raw / 1_000_000.0) - CHROME_EPOCH_OFFSET_SECONDS
    if unix_sec < 946_684_800 or unix_sec > 4_102_444_800:
        return None
    return datetime.fromtimestamp(unix_sec, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def unix_expires_to_utc(value: Any) -> str | None:
    try:
        exp = float(value)
    except (TypeError, ValueError):
        return None
    if exp <= 0:
        return None
    try:
        return datetime.fromtimestamp(exp, tz=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (OverflowError, OSError, ValueError):
        return None


def extract_search_query(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.netloc or "").casefold()
    query = parse_qs(parsed.query)
    if any(token in host for token in _SEARCH_HOSTS_Q) and query.get("q"):
        return query["q"][0]
    if "yahoo.com" in host and query.get("p"):
        return query["p"][0]
    return None


def classify_history_tier(url: str | None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return "partial"
    if "[*.]" in raw or "*" in raw:
        return "partial"
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "partial"
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.netloc:
        return "partial"
    path = parsed.path.rstrip("/")
    if path or parsed.query:
        return "full"
    return "partial"


@dataclass(frozen=True, slots=True)
class BrowserHistoryItem:
    record_id: str
    history_tier: str
    url: str | None
    title: str | None
    observed_at: str | None
    visit_count: int | None
    evidence_type: str
    source_label: str
    search_query: str | None
    extra: dict[str, Any] = field(default_factory=dict)

    def preview_text(self) -> str:
        parts = [
            self.title or "",
            self.url or "",
            self.search_query or "",
            self.source_label,
            self.evidence_type,
        ]
        return "\n".join(part for part in parts if part).strip()


class CdpWebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._buf = bytearray()

    @classmethod
    async def connect(cls, host: str, port: int, path: str, *, timeout: float) -> CdpWebSocket:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
            if not chunk:
                break
            header += chunk
        if b" 101 " not in header.split(b"\r\n", 1)[0]:
            writer.close()
            await writer.wait_closed()
            raise ConnectionError("CDP websocket upgrade failed")
        leftover = header.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in header else b""
        client = cls(reader, writer)
        client._buf.extend(leftover)
        return client

    async def send(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        length = len(data)
        mask = os.urandom(4)
        frame = bytearray([0x81])
        if length <= 125:
            frame.append(0x80 | length)
        elif length <= 65535:
            frame.append(0x80 | 126)
            frame.extend(length.to_bytes(2, "big"))
        else:
            frame.append(0x80 | 127)
            frame.extend(length.to_bytes(8, "big"))
        frame.extend(mask)
        masked = bytearray(data)
        for index, byte in enumerate(masked):
            masked[index] = byte ^ mask[index % 4]
        frame.extend(masked)
        self._writer.write(frame)
        await self._writer.drain()

    async def _read_exact(self, size: int) -> bytes:
        while len(self._buf) < size:
            chunk = await self._reader.read(max(4096, size - len(self._buf)))
            if not chunk:
                break
            self._buf.extend(chunk)
        result = bytes(self._buf[:size])
        del self._buf[:size]
        return result

    async def recv(self) -> dict[str, Any] | None:
        header = await self._read_exact(2)
        if len(header) < 2:
            return None
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        masked = bool(header[1] & 0x80)
        if length == 126:
            ext = await self._read_exact(2)
            length = int.from_bytes(ext, "big")
        elif length == 127:
            ext = await self._read_exact(8)
            length = int.from_bytes(ext, "big")
        mask = await self._read_exact(4) if masked else b""
        payload = bytearray(await self._read_exact(length))
        if masked:
            for index in range(len(payload)):
                payload[index] ^= mask[index % 4]
        if opcode in {0x8}:
            return None
        if opcode in {0x9, 0xA}:
            return await self.recv()
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    async def recv_id(self, message_id: int, *, max_messages: int = 80) -> dict[str, Any] | None:
        for _ in range(max_messages):
            message = await self.recv()
            if message is None:
                return None
            if message.get("id") == message_id:
                return message
        return None

    async def close(self) -> None:
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except OSError:
            return


async def fetch_tabs(host: str, port: int, *, timeout: float) -> list[dict[str, Any]]:
    url = f"http://{host}:{port}/json"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


async def wait_devtools_ready(host: str, port: int, *, attempts: int = 10) -> bool:
    url = f"http://{host}:{port}/json/version"
    for _ in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.get(url)
            if response.status_code == 200:
                return True
        except (httpx.HTTPError, OSError):
            await asyncio.sleep(0.5)
    return False


async def _page_evaluate(ws: CdpWebSocket, session_id: str, expression: str, message_id: int) -> Any:
    await ws.send(
        {
            "id": message_id,
            "sessionId": session_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }
    )
    result = await ws.recv_id(message_id)
    if not result:
        return None
    return result.get("result", {}).get("result", {}).get("value")


async def _open_inspect_page(
    browser_ws: CdpWebSocket,
    url: str,
    create_id: int,
    attach_id: int,
    *,
    settle_s: float,
) -> tuple[str, str] | None:
    await browser_ws.send({"id": create_id, "method": "Target.createTarget", "params": {"url": url}})
    created = await browser_ws.recv_id(create_id)
    if not created or "result" not in created:
        return None
    target_id = str(created["result"].get("targetId") or "")
    if not target_id:
        return None
    await browser_ws.send(
        {
            "id": attach_id,
            "method": "Target.attachToTarget",
            "params": {"targetId": target_id, "flatten": True},
        }
    )
    attached = await browser_ws.recv_id(attach_id)
    if not attached or "result" not in attached:
        return None
    session_id = str(attached["result"].get("sessionId") or "")
    if not session_id:
        return None
    await asyncio.sleep(settle_s)
    return target_id, session_id


async def extract_tab_histories(
    host: str,
    port: int,
    tabs: list[dict[str, Any]],
    *,
    timeout: float,
) -> list[BrowserHistoryItem]:
    items: list[BrowserHistoryItem] = []
    for tab in tabs:
        tab_id = str(tab.get("id") or "")
        if not tab_id:
            continue
        ws: CdpWebSocket | None = None
        try:
            ws = await CdpWebSocket.connect(
                host, port, f"/devtools/page/{tab_id}", timeout=min(timeout, 8.0)
            )
            await ws.send({"id": 1, "method": "Page.getNavigationHistory"})
            result = await ws.recv_id(1)
        except (OSError, ConnectionError, asyncio.TimeoutError):
            continue
        finally:
            if ws is not None:
                await ws.close()
        if not result or "result" not in result:
            continue
        for index, entry in enumerate(result["result"].get("entries") or []):
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "").strip() or None
            title = str(entry.get("title") or "").strip() or None
            query = extract_search_query(url or "")
            items.append(
                BrowserHistoryItem(
                    record_id=f"tab-{tab_id}-{index}",
                    history_tier=classify_history_tier(url),
                    url=url,
                    title=title,
                    observed_at=None,
                    visit_count=None,
                    evidence_type="tab_navigation",
                    source_label="Active Tab Navigation Stack (CDP)",
                    search_query=query,
                    extra={"transition_type": entry.get("transitionType")},
                )
            )
    return items


async def extract_cookie_domains(
    host: str,
    port: int,
    tabs: list[dict[str, Any]],
    *,
    timeout: float,
) -> list[BrowserHistoryItem]:
    if not tabs:
        return []
    tab_id = str(tabs[0].get("id") or "")
    if not tab_id:
        return []
    ws: CdpWebSocket | None = None
    cookies: list[dict[str, Any]] = []
    try:
        ws = await CdpWebSocket.connect(
            host, port, f"/devtools/page/{tab_id}", timeout=min(timeout, 8.0)
        )
        await ws.send({"id": 1, "method": "Network.enable"})
        await ws.recv_id(1)
        await ws.send({"id": 2, "method": "Network.getAllCookies"})
        result = await ws.recv_id(2)
        if result and "result" in result:
            raw = result["result"].get("cookies") or []
            cookies = [item for item in raw if isinstance(item, dict)]
    except (OSError, ConnectionError, asyncio.TimeoutError):
        return []
    finally:
        if ws is not None:
            await ws.close()
    items: list[BrowserHistoryItem] = []
    for index, cookie in enumerate(cookies):
        domain = str(cookie.get("domain") or "").lstrip(".")
        path = str(cookie.get("path") or "/")
        origin = f"https://{domain}{path}" if domain else None
        items.append(
            BrowserHistoryItem(
                record_id=f"cookie-{index}",
                history_tier="partial",
                url=origin,
                title=None,
                observed_at=unix_expires_to_utc(cookie.get("expires")),
                visit_count=None,
                evidence_type="cookie_metadata",
                source_label="Network Cookie Store (CDP)",
                search_query=None,
                extra={
                    "domain": domain,
                    "path": path,
                    "secure": bool(cookie.get("secure")),
                    "http_only": bool(cookie.get("httpOnly")),
                    "size": cookie.get("size"),
                },
            )
        )
    return items


async def extract_ntp_tiles(browser_ws: CdpWebSocket) -> list[BrowserHistoryItem]:
    opened = await _open_inspect_page(
        browser_ws, "chrome://ntp-tiles-internals", 10, 11, settle_s=1.0
    )
    if opened is None:
        return []
    target_id, session_id = opened
    raw_text = await _page_evaluate(
        browser_ws, session_id, "document.body ? document.body.innerText : ''", 12
    )
    await browser_ws.send({"id": 13, "method": "Target.closeTarget", "params": {"targetId": target_id}})
    await browser_ws.recv_id(13)
    if not isinstance(raw_text, str):
        return []
    items: list[BrowserHistoryItem] = []
    for index, block in enumerate(raw_text.split("Source\tTOP_SITES")[1:]):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                fields[parts[0].strip()] = parts[1].strip()
        url = fields.get("URL")
        if not url:
            continue
        visit_raw = fields.get("Visit Count", "")
        items.append(
            BrowserHistoryItem(
                record_id=f"ntp-{index}",
                history_tier=classify_history_tier(url),
                url=url,
                title=None,
                observed_at=None,
                visit_count=int(visit_raw) if visit_raw.isdigit() else None,
                evidence_type="top_site_history",
                source_label="NTP Top Sites Engine",
                search_query=extract_search_query(url),
                extra={"score": fields.get("Score")},
            )
        )
    return items


async def extract_prefs_engagement(browser_ws: CdpWebSocket) -> list[BrowserHistoryItem]:
    opened = await _open_inspect_page(
        browser_ws, "chrome://prefs-internals", 20, 21, settle_s=1.5
    )
    if opened is None:
        return []
    target_id, session_id = opened
    raw_json = await _page_evaluate(
        browser_ws, session_id, "document.body ? document.body.innerText : ''", 22
    )
    await browser_ws.send({"id": 23, "method": "Target.closeTarget", "params": {"targetId": target_id}})
    await browser_ws.recv_id(23)
    try:
        prefs = json.loads(raw_json) if isinstance(raw_json, str) else {}
    except json.JSONDecodeError:
        return []
    if not isinstance(prefs, dict):
        return []
    exceptions = (
        prefs.get("profile", {}).get("content_settings", {}).get("exceptions", {})
    )
    items: list[BrowserHistoryItem] = []
    site_eng = exceptions.get("site_engagement", {}).get("value", {})
    if isinstance(site_eng, dict):
        for index, (origin, data) in enumerate(site_eng.items()):
            if str(origin).startswith("chrome://") or not isinstance(data, dict):
                continue
            setting = data.get("setting", {})
            points = setting.get("points") if isinstance(setting, dict) else None
            last_eng = setting.get("lastEngagementTime") if isinstance(setting, dict) else 0
            clean = str(origin).split(",*")[0].strip()
            items.append(
                BrowserHistoryItem(
                    record_id=f"eng-{index}",
                    history_tier=classify_history_tier(clean),
                    url=clean,
                    title=None,
                    observed_at=chrome_time_to_utc(last_eng),
                    visit_count=None,
                    evidence_type="site_engagement",
                    source_label="Site Engagement Cache (chrome://prefs-internals)",
                    search_query=extract_search_query(clean),
                    extra={"points": points},
                )
            )
    ordinal = 0
    for category, content in exceptions.items():
        if category == "site_engagement" or not isinstance(content, dict):
            continue
        values = content.get("value", {})
        if not isinstance(values, dict):
            continue
        for origin, data in values.items():
            if str(origin).startswith("chrome://") or not isinstance(data, dict):
                continue
            last_modified = chrome_time_to_utc(data.get("last_modified"))
            if not last_modified:
                continue
            clean = str(origin).split(",*")[0].strip()
            items.append(
                BrowserHistoryItem(
                    record_id=f"pref-{ordinal}",
                    history_tier=classify_history_tier(clean),
                    url=clean,
                    title=None,
                    observed_at=last_modified,
                    visit_count=None,
                    evidence_type="content_settings",
                    source_label=f"Content Settings: {category}",
                    search_query=None,
                    extra={"category": str(category)},
                )
            )
            ordinal += 1
    return items


_PREDICTOR_JS = """
(function() {
    var tables = document.querySelectorAll('table');
    var allData = [];
    tables.forEach(function(t, tableIdx) {
        var headerRow = t.querySelector('tr');
        var colNames = [];
        if (headerRow) {
            headerRow.querySelectorAll('th, td').forEach(function(c) {
                colNames.push(c.textContent.trim());
            });
        }
        var rows = t.querySelectorAll('tr');
        for (var i = 1; i < rows.length; i++) {
            var cells = rows[i].querySelectorAll('td');
            var rowObj = {_table: tableIdx};
            cells.forEach(function(c, ci) {
                var key = ci < colNames.length ? colNames[ci] : 'col_' + ci;
                rowObj[key] = c.textContent.trim();
            });
            allData.push(rowObj);
        }
    });
    return JSON.stringify(allData);
})()
"""


async def extract_predictors(browser_ws: CdpWebSocket) -> list[BrowserHistoryItem]:
    opened = await _open_inspect_page(
        browser_ws, "chrome://predictors", 30, 31, settle_s=1.5
    )
    if opened is None:
        return []
    target_id, session_id = opened
    raw_json = await _page_evaluate(browser_ws, session_id, _PREDICTOR_JS, 32)
    await browser_ws.send({"id": 33, "method": "Target.closeTarget", "params": {"targetId": target_id}})
    await browser_ws.recv_id(33)
    try:
        rows = json.loads(raw_json) if isinstance(raw_json, str) else []
    except json.JSONDecodeError:
        return []
    items: list[BrowserHistoryItem] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        host = str(row.get("Host") or "")
        origin = str(row.get("Origin") or "")
        url_col = str(row.get("URL") or "")
        hits = row.get("Num Hits", row.get("Hit Count", 0))
        target_url = ""
        if url_col.startswith("http"):
            target_url = url_col
        elif origin.startswith("http"):
            target_url = origin
        elif host.startswith("http"):
            target_url = host
        elif "." in host and not host.startswith(" "):
            target_url = f"https://{host}/"
        if not target_url:
            continue
        items.append(
            BrowserHistoryItem(
                record_id=f"pred-{index}",
                history_tier=classify_history_tier(target_url),
                url=target_url,
                title=None,
                observed_at=None,
                visit_count=int(hits) if str(hits).isdigit() else None,
                evidence_type="predictor_history",
                source_label="Predictors Engine (chrome://predictors)",
                search_query=extract_search_query(target_url),
                extra={"host": host, "score": row.get("Score")},
            )
        )
    return items


_WEB_LINE = re.compile(r"^(URI:|Manifest URL:|Package name:|Scope:|Origin:|Script:)")


async def extract_web_registries(browser_ws: CdpWebSocket) -> list[BrowserHistoryItem]:
    items: list[BrowserHistoryItem] = []
    for create_id, attach_id, eval_id, close_id, url, settle, label in (
        (40, 41, 42, 43, "chrome://webapks", 0.8, "WebAPK/PWA Registry"),
        (70, 71, 72, 73, "chrome://serviceworker-internals", 0.8, "ServiceWorker Registry"),
    ):
        opened = await _open_inspect_page(
            browser_ws, url, create_id, attach_id, settle_s=settle
        )
        if opened is None:
            continue
        target_id, session_id = opened
        text = await _page_evaluate(
            browser_ws, session_id, "document.body ? document.body.innerText : ''", eval_id
        )
        await browser_ws.send(
            {"id": close_id, "method": "Target.closeTarget", "params": {"targetId": target_id}}
        )
        await browser_ws.recv_id(close_id)
        if not isinstance(text, str):
            continue
        for index, line in enumerate(text.splitlines()):
            stripped = line.strip()
            if not _WEB_LINE.match(stripped):
                continue
            maybe_url = stripped.split(":", 1)[-1].strip()
            items.append(
                BrowserHistoryItem(
                    record_id=f"web-{create_id}-{index}",
                    history_tier=classify_history_tier(maybe_url),
                    url=maybe_url if maybe_url.startswith("http") else None,
                    title=stripped[:180],
                    observed_at=None,
                    visit_count=None,
                    evidence_type="web_registry",
                    source_label=label,
                    search_query=None,
                    extra={"entry": stripped[:240]},
                )
            )
    return items


async def collect_chrome_history(
    host: str,
    port: int,
    *,
    timeout: float,
) -> list[BrowserHistoryItem]:
    tabs = await fetch_tabs(host, port, timeout=min(timeout, 8.0))
    items: list[BrowserHistoryItem] = []
    items.extend(await extract_tab_histories(host, port, tabs, timeout=timeout))
    items.extend(await extract_cookie_domains(host, port, tabs, timeout=timeout))
    browser_ws = await CdpWebSocket.connect(host, port, "/devtools/browser", timeout=timeout)
    try:
        items.extend(await extract_ntp_tiles(browser_ws))
        items.extend(await extract_prefs_engagement(browser_ws))
        items.extend(await extract_predictors(browser_ws))
        items.extend(await extract_web_registries(browser_ws))
    finally:
        await browser_ws.close()
    return items
