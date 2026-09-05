"""Offline English-to-Chinese RSS translation; no API key or Folo credentials."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import ctranslate2
from bs4 import BeautifulSoup
from defusedxml import ElementTree as SafeET
import requests
import sentencepiece as spm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
ROOT = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", "/data"))
MODELS = Path(os.environ.get("MODEL_DIR", "/models"))
SOURCES = json.loads((ROOT / "sources.json").read_text("utf-8"))
if os.environ.get("RSSHUB_BASE_URL"):
    for source in SOURCES:
        source["url"] = os.environ["RSSHUB_BASE_URL"].rstrip("/") + urlsplit(source["url"]).path
INTERVAL = max(300, int(os.environ.get("REFRESH_SECONDS", "1800")))
PORT = int(os.environ.get("PORT", "1201"))
MODEL_ID = "argos-en-zh-1.9-ct2-v3"
STATE = {"service": "folo-rss-translate", "model_ready": False, "feeds": {}, "interval_seconds": INTERVAL}
STATE_LOCK = threading.Lock()
NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"
NS_DC = "http://purl.org/dc/elements/1.1/"
ET.register_namespace("content", NS_CONTENT)
ET.register_namespace("dc", NS_DC)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: Path, content: bytes):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def save_status():
    with STATE_LOCK:
        content = json.dumps(STATE, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write(DATA / "status.json", content)


def plain_text(value: str):
    soup = BeautifulSoup(value or "", "html.parser")
    for element in soup(["script", "style", "iframe", "noscript"]):
        element.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


class Translator:
    def __init__(self):
        metadata_paths = list(MODELS.rglob("metadata.json"))
        candidates = []
        for path in metadata_paths:
            metadata = json.loads(path.read_text("utf-8"))
            if metadata.get("from_code") == "en" and metadata.get("to_code") == "zh":
                candidates.append((path.parent, metadata))
        if len(candidates) != 1:
            raise RuntimeError("Expected exactly one official Argos en→zh model package")
        directory, metadata = candidates[0]
        self.prefix = metadata.get("target_prefix", "")
        self.tokenizer = spm.SentencePieceProcessor(model_file=str(directory / "sentencepiece.model"))
        self.engine = ctranslate2.Translator(str(directory / "model"), device="cpu", compute_type="int8", inter_threads=1, intra_threads=4)
        self.db = sqlite3.connect(DATA / "translations.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS translations (key TEXT PRIMARY KEY, text TEXT NOT NULL)")

    def translate(self, text: str):
        text = text.strip()
        if not text or not re.search(r"[A-Za-z]{2}", text):
            return text
        key = hashlib.sha256((MODEL_ID + "\0" + text).encode()).hexdigest()
        cached = self.db.execute("SELECT text FROM translations WHERE key=?", (key,)).fetchone()
        if cached:
            return cached[0]
        # Split on sentence boundaries, then bound token count so long text is
        # never silently truncated by the inference engine.
        segments = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])|\n+", text)
        tokenized = []
        for segment in segments:
            tokens = self.tokenizer.encode(segment, out_type=str)
            tokenized.extend(tokens[start:start + 200] for start in range(0, len(tokens), 200))
        if not tokenized:
            return text
        translations = self.engine.translate_batch(
            tokenized, target_prefix=[[self.prefix]] * len(tokenized) if self.prefix else None,
            replace_unknowns=True, beam_size=4, length_penalty=1.0,
            max_input_length=512, max_decoding_length=512, max_batch_size=512, batch_type="tokens",
        )
        parts = []
        for result in translations:
            translated = self.tokenizer.decode_pieces(result.hypotheses[0]).replace("▁", " ").replace("_", " ").strip()
            if self.prefix and translated.startswith(self.prefix):
                translated = translated[len(self.prefix):].strip()
            parts.append(translated)
        output = "\n".join(parts)
        if not output:
            raise RuntimeError("Translation returned empty text; preserving previous feed")
        self.db.execute("INSERT OR REPLACE INTO translations VALUES (?,?)", (key, output))
        self.db.commit()
        return output


def get_feed(url):
    with requests.get(url, timeout=(10, 60), stream=True, headers={"User-Agent": "Personal-Folo-RSS-Translate/1.0"}) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > 10 * 1024 * 1024:
                raise ValueError("Source feed exceeds 10 MB")
    root = SafeET.fromstring(data)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("Source must be an RSS 2.0 feed")
    return channel


def paragraph_html(text):
    return "".join("<p>" + html.escape(line) + "</p>" for line in text.splitlines() if line)


def make_feed(source, channel, translator):
    items = channel.findall("item")[:source["max_items"]]
    if not items:
        raise ValueError("Source feed is empty; preserving previous output")
    root = ET.Element("rss", version="2.0")
    out = ET.SubElement(root, "channel")
    ET.SubElement(out, "title").text = source["title"]
    ET.SubElement(out, "link").text = source["site"]
    ET.SubElement(out, "description").text = "本地机器翻译的订阅内容，中英对照。翻译可能不准确，请以原文为准；不代表已抓取原站全文。"
    ET.SubElement(out, "language").text = "zh-CN"
    ET.SubElement(out, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc), usegmt=True)
    ET.SubElement(out, "ttl").text = str(INTERVAL // 60)
    for item in items:
        original_title = plain_text(item.findtext("title", ""))
        body = plain_text(item.findtext("{" + NS_CONTENT + "}encoded") or item.findtext("description", ""))
        link = item.findtext("link", "")
        if urlsplit(link).scheme not in ("https", "http"):
            link = source["site"]
        translated_title = translator.translate(original_title)
        translated_body = translator.translate(body) if body else ""
        entry = ET.SubElement(out, "item")
        ET.SubElement(entry, "title").text = translated_title
        ET.SubElement(entry, "link").text = link
        original_id = item.findtext("guid") or link or original_title
        guid = "urn:folo-translate:" + source["id"] + ":" + hashlib.sha256(original_id.encode()).hexdigest()
        ET.SubElement(entry, "guid", isPermaLink="false").text = guid
        for field in ("pubDate", "author", "{" + NS_DC + "}creator", "copyright"):
            value = item.findtext(field)
            if value:
                ET.SubElement(entry, field).text = value
        for category in item.findall("category"):
            ET.SubElement(entry, "category").text = category.text
        markup = "<p><em>本地机器翻译 · 原文附后</em></p>" + paragraph_html(translated_body)
        markup += "<hr/><h3>原文：" + html.escape(original_title) + "</h3>" + paragraph_html(body)
        markup += '<p><a href="' + html.escape(link, quote=True) + '">阅读原文</a></p>'
        ET.SubElement(entry, "description").text = markup
        logging.info("%s: %s", source["id"], translated_title[:100])
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), len(items)


def worker():
    try:
        translator = Translator()
        with STATE_LOCK:
            STATE["model_ready"] = True
        save_status()
    except Exception as exc:
        logging.exception("Cannot load translation model")
        with STATE_LOCK:
            STATE["model_error"] = str(exc)
        save_status()
        return
    while True:
        for source in SOURCES:
            try:
                channel = get_feed(source["url"])
                output, count = make_feed(source, channel, translator)
                atomic_write(DATA / (source["id"] + ".xml"), output)
                with STATE_LOCK:
                    STATE["feeds"][source["id"]] = {"ok": True, "items": count, "updated_at": utcnow()}
            except Exception as exc:
                logging.exception("Failed to refresh %s", source["id"])
                with STATE_LOCK:
                    previous = STATE["feeds"].get(source["id"], {})
                    STATE["feeds"][source["id"]] = {**previous, "ok": False, "error": str(exc), "failed_at": utcnow()}
            save_status()
        with STATE_LOCK:
            healthy = all(STATE["feeds"].get(s["id"], {}).get("ok") for s in SOURCES)
        time.sleep(INTERVAL if healthy else 60)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            with STATE_LOCK:
                snapshot = json.dumps(STATE, ensure_ascii=False).encode()
                ok = STATE["model_ready"] and all(STATE["feeds"].get(s["id"], {}).get("ok") for s in SOURCES)
            return self.send(200 if ok else 503, "application/json; charset=utf-8", snapshot)
        allowed = {"/" + s["id"] + ".xml" for s in SOURCES}
        if path in allowed:
            file = DATA / path.lstrip("/")
            if file.exists():
                return self.send(200, "application/rss+xml; charset=utf-8", file.read_bytes())
            return self.send(503, "text/plain; charset=utf-8", "首次翻译尚未完成，请稍后重试。".encode())
        if path == "/":
            links = "".join('<li><a href="/' + s["id"] + '.xml">' + html.escape(s["title"]) + '</a></li>' for s in SOURCES)
            page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>本地 RSS 翻译</title><style>body{font:18px/1.8 system-ui;max-width:760px;margin:60px auto;padding:24px}a{color:#165acb}</style><h1>本地 RSS 翻译</h1><p>免费英译中，每 30 分钟检查新内容，保留原文。翻译质量以实际结果为准。</p><ul>' + links + '</ul><p><a href="/healthz">查看更新状态</a></p><p>Folo 需要公网可访问的订阅地址。此本地页面仅用于预览和验证，发布接入尚需配置。</p></html>'
            return self.send(200, "text/html; charset=utf-8", page.encode())
        return self.send(404, "text/plain", b"Not found")

    def send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    logging.info("Listening on port %s", PORT)
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), PORT), Handler).serve_forever()
