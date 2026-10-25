import json
import os
import random
import sys
import time
from urllib.parse import urljoin, urlparse, urlunparse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Infrastructure subdomains to ignore
IGNORED_SUBDOMAINS = {
    "community",
    "www",
    "support",
    "auth",
    "global",
    "forums",
    "help",
    "link",
    "my",
    "services",
    "ucp",
    "ar",
    "de",
    "es",
    "fr",
    "it",
    "ja",
    "pl",
    "pt-br",
    "ru",
    "zh",
    "blog",
    "news",
    "fandom",
}

STATE_FILE = "crawler_state.json"
WIKIS_FILE = "fandom_statistics_urls.txt"
SITEMAPS_FILE = "visited_sitemaps.txt"


def clean_url_string(url):
    """Removes URL fragments (# anchors) to prevent duplicate processing."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def load_saved_sets():
    """Generates/populates seen_wikis and visited_sitemaps directly

    from the saved text files on disk.
    """
    seen_wikis = set()
    visited_sitemaps = set()

    # 1. Load already discovered wikis
    if os.path.exists(WIKIS_FILE):
        try:
            with open(WIKIS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "fandom.com/wiki/Special:Statistics" in line:
                        subdomain = line.split(
                            "https://")[1].split(".fandom.com")[0]
                        seen_wikis.add(subdomain.lower())
            print(
                f"[*] Generated & loaded {len(seen_wikis):,} unique wikis from file.")
        except Exception as e:
            print(f"[!] Error reading {WIKIS_FILE}: {e}")

    # 2. Load already visited sitemap lists/ranges
    if os.path.exists(SITEMAPS_FILE):
        try:
            with open(SITEMAPS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    url = line.strip()
                    if url:
                        visited_sitemaps.add(url)
            print(
                f"[*] Generated & loaded {len(visited_sitemaps)                                          :,} visited sitemaps"
                " from file."
            )
        except Exception as e:
            print(f"[!] Error reading {SITEMAPS_FILE}: {e}")

    return seen_wikis, visited_sitemaps


def load_crawler_queue(visited_sitemaps):
    """Loads the active queue from JSON state,

    or falls back to default if starting fresh.
    """
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                queue = state.get(
                    "queue", ["https://community.fandom.com/wiki/Sitemap"])
                print(
                    f"[*] Resumed queue state: {len(queue):,} items pending.")
                return queue
        except Exception as e:
            print(f"[!] Error loading {STATE_FILE}: {
                  e}. Starting fresh queue.")

    return ["https://community.fandom.com/wiki/Sitemap"]


def save_crawler_state(queue):
    state = {"queue": queue}
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[!] Error saving state: {e}")


def crawl_fandom_sitemap():
    # Generate sets automatically from text files on startup
    seen_wikis, visited_sitemaps = load_saved_sets()
    queue = load_crawler_queue(visited_sitemaps)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
                " like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        print("[*] Starting crawl with persistent list saving and set generation...")
        print(
            "[*] If you stop the script (Ctrl+C), all visited lists and wikis are"
            " safely on disk."
        )

        iteration_count = 0

        try:
            while queue:
                current_url = clean_url_string(queue.pop(0))
                if current_url in visited_sitemaps:
                    continue
                visited_sitemaps.add(current_url)

                # Immediately log this visited sitemap to the text file live
                with open(SITEMAPS_FILE, "a", encoding="utf-8") as sf:
                    sf.write(current_url + "\n")

                try:
                    page.goto(current_url, timeout=60000)

                    # Check if Cloudflare caught the page
                    title = page.title()
                    html_content = page.content()

                    if (
                        "Just a moment" in title
                        or "Attention Required" in title
                        or "cf-chl" in html_content
                    ):
                        print("\n" + "=" * 60)
                        print(f"[!] CLOUDFLARE CHALLENGE TRIGGERED ON: {
                              current_url}")
                        print(
                            "[!] Please go to the browser window and solve the challenge.")
                        print(
                            "[!] Once solved and the sitemap page loads, come back here and"
                            " press ENTER."
                        )
                        print("=" * 60)
                        input("Press [Enter] to resume crawling...")
                        time.sleep(2)

                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass

                except Exception as e:
                    print(f"[!] Error loading {current_url}: {e}")
                    continue

                html_content = page.content()
                soup = BeautifulSoup(html_content, "html.parser")
                anchor_tags = soup.find_all("a", href=True)

                page_wikis = 0
                sub_sitemaps_found = 0

                # Open wikis file in append mode ('a') for live writing
                with open(WIKIS_FILE, "a", encoding="utf-8") as wf:
                    for a in anchor_tags:
                        href = a["href"]
                        if not href:
                            continue

                        full_url = clean_url_string(urljoin(current_url, href))
                        parsed_url = urlparse(full_url)
                        netloc = parsed_url.netloc.lower()

                        # Case 1: Sub-sitemap pagination/range link
                        if "community.fandom.com" in netloc:
                            if any(
                                kw in parsed_url.path or kw in parsed_url.query
                                for kw in ["Sitemap", "level=", "from=", "to="]
                            ):
                                if not any(
                                    x in full_url
                                    for x in ["auth.fandom.com", "signin", "register"]
                                ):
                                    if full_url not in visited_sitemaps and full_url not in queue:
                                        queue.append(full_url)
                                        sub_sitemaps_found += 1

                        # Case 2: Actual user wiki link (*.fandom.com)
                        elif netloc.endswith(".fandom.com"):
                            subdomain = netloc.split(".fandom.com")[
                                0].split(".")[0].lower()
                            if subdomain and subdomain not in IGNORED_SUBDOMAINS:
                                if subdomain not in seen_wikis:
                                    seen_wikis.add(subdomain)
                                    stat_url = (
                                        f"https://{subdomain}.fandom.com/wiki/Special:Statistics\n"
                                    )
                                    wf.write(stat_url)
                                    page_wikis += 1

                if page_wikis > 0:
                    print(
                        f"[+] Leaf page reached! Found {
                            page_wikis} new | Total unique"
                        f" wikis: {len(seen_wikis):,}"
                    )
                else:
                    print(
                        f"[-] Directory range page | Added {
                            sub_sitemaps_found} sub-ranges"
                        f" | Queue size: {len(queue):,}"
                    )

                # Save queue state every 20 pages
                iteration_count += 1
                if iteration_count >= 20:
                    save_crawler_state(queue)
                    iteration_count = 0

                # Polite random delay between requests
                time.sleep(random.uniform(1.0, 2.0))

        except KeyboardInterrupt:
            print(
                "\n[!] Interrupted by user (Ctrl+C). Saving current queue state..."
            )
            save_crawler_state(queue)
            print("[+] State saved successfully. You can resume anytime!")
            sys.exit(0)

        browser.close()
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)


if __name__ == "__main__":
    start_time = time.time()
    crawl_fandom_sitemap()
    elapsed = time.time() - start_time
    print(f"\n[+] Crawl session finished in {elapsed:.2f} seconds!")
