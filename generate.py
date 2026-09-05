"""Build all public feeds once; failures prevent the publication step."""
import json
import os
from pathlib import Path

from app import DATA, SOURCES, Translator, atomic_write, get_feed, make_feed, utcnow


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    output_dir = Path(os.environ.get("OUTPUT_DIR", "public"))
    translator = Translator()
    outputs = {}
    counts = {}
    try:
        for source in SOURCES:
            content, count = make_feed(source, get_feed(source["url"]), translator)
            outputs[source["id"] + ".xml"] = content
            counts[source["id"]] = count
    finally:
        translator.db.close()
    # Do not touch any published file until every source succeeds.
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in outputs.items():
        atomic_write(output_dir / name, content)
    atomic_write(output_dir / "status.json", json.dumps(
        {"updated_at": utcnow(), "items": counts}, ensure_ascii=False, indent=2
    ).encode("utf-8"))
    print("Feeds ready:", counts)


if __name__ == "__main__":
    main()
