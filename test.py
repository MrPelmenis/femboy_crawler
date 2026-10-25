
import csv
import os
import random
import time
import requests
from urllib.parse import urlparse

INPUT_FILE = "fandom_statistics_urls.txt"
OUTPUT_FILE = "fandom_dumps.csv"
CHECKPOINT_FILE = "visited.txt"

PROGRESS_INTERVAL = 10  # Print progress every 10 processed URLs
REQUEST_DELAY = 0

USER_AGENTS = [
    "FandomDumpResearch/1.0",
    "Mozilla/5.0 (compatible; DumpLinkChecker/1.0)",
    "FandomArchiveLinkChecker/1.0",
    "Mozilla/5.0 (compatible; WikiResearch/1.0)",
    "DumpAvailabilityChecker/1.0",
    "Mozilla/5.0 (compatible; ArchiveResearch/1.0)",
    "WikiDumpAudit/1.0",
    "FandomMetadataResearch/1.0",
    "Mozilla/5.0 (compatible; SiteStatisticsResearch/1.0)",
    "PublicDumpVerifier/1.0",
]

FIELDS = ["wiki_url", "wikiid", "dump_url", "http_status", "valid"]

session = requests.Session()
session.headers["User-Agent"] = random.choice(USER_AGENTS)


def get_wikiid(wiki_url):
    parsed = urlparse(wiki_url.strip())
    host = parsed.netloc

    if not host or not host.endswith("fandom.com"):
        raise ValueError("Invalid Fandom URL")

    response = session.get(
        f"https://{host}/api.php",
        params={
            "action": "query",
            "meta": "siteinfo",
            "siprop": "general",
            "format": "json",
        },
        timeout=(10, 20),
    )
    response.raise_for_status()

    data = response.json()
    return data["query"]["general"]["wikiid"]


def make_dump_url(wikiid):
    filename = f"{wikiid}_pages_current.xml.7z"
    return (
        "https://s3.amazonaws.com/wikia_xml_dumps/"
        f"{wikiid[0].lower()}/{wikiid[:2].lower()}/{filename}"
    )


def check_dump(url):
    try:
        with session.get(
            url,
            stream=True,
            allow_redirects=True,
            timeout=(10, 30),
        ) as response:
            status = response.status_code

            # Only accept 2xx responses with a non-empty body,
            # reading no more than one byte.
            if not 200 <= status < 300:
                return None

            first_byte = next(
                response.iter_content(chunk_size=1),
                b"",
            )

            if not first_byte:
                return None

            return {
                "http_status": status,
                "final_url": response.url,
            }

    except requests.RequestException:
        return None


def load_visited():
    if not os.path.exists(CHECKPOINT_FILE):
        return set()

    with open(CHECKPOINT_FILE, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def mark_visited(url):
    with open(CHECKPOINT_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")
        f.flush()
        os.fsync(f.fileno())


def main():
    with open(INPUT_FILE, encoding="utf-8") as f:
        urls = [
            line.strip()
            for line in f
            if line.strip() and not line.lstrip().startswith("#")
        ]

    visited = load_visited()
    pending = [url for url in urls if url not in visited]
    total = len(urls)
    processed = len(visited.intersection(urls))

    output_exists = (
        os.path.exists(OUTPUT_FILE)
        and os.path.getsize(OUTPUT_FILE) > 0
    )

    last_rotation = time.monotonic()
    last_progress = time.monotonic()

    with open(
        OUTPUT_FILE,
        "a",
        newline="",
        encoding="utf-8",
        buffering=1,
    ) as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDS)

        if not output_exists:
            writer.writeheader()
            csv_file.flush()
            os.fsync(csv_file.fileno())

        for url in pending:
            now = time.monotonic()

            if now - last_rotation >= 60:
                session.headers["User-Agent"] = random.choice(USER_AGENTS)
                last_rotation = now

            try:
                wikiid = get_wikiid(url)
            except Exception as exc:
                print(f"\n[API ERROR] {url}: {exc}", flush=True)
                mark_visited(url)
                visited.add(url)
                processed += 1
                continue

            dump_url = make_dump_url(wikiid)
            result = check_dump(dump_url)

            # Only write verified, non-empty 2xx responses.
            if result is not None:
                writer.writerow({
                    "wiki_url": url,
                    "wikiid": wikiid,
                    "dump_url": dump_url,
                    "http_status": result["http_status"],
                    "valid": True,
                })
                csv_file.flush()
                os.fsync(csv_file.fileno())

            mark_visited(url)
            visited.add(url)
            processed += 1

            now = time.monotonic()
            if (
                processed % PROGRESS_INTERVAL == 0
                or now - last_progress >= 10
                or processed == total
            ):
                print(
                    f"\rProgress: {processed}/{total} "
                    f"({processed / total * 100:.1f}%)",
                    end="\n",
                    flush=True,
                )
                last_progress = now

            time.sleep(REQUEST_DELAY)

    print(f"\nFinished. Valid dump links saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
