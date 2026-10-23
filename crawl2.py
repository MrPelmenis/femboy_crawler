import random
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


def clean_url_string(url):
    """Removes URL fragments (# anchors) to prevent duplicate processing."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def crawl_fandom_sitemap():
    seen_wikis = set()
    visited_sitemaps = set()
    queue = ["https://community.fandom.com/wiki/Sitemap"]

    with sync_playwright() as p:
        # headless=False opens a visible browser window so you can interact with it!
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

        print("[*] Starting interactive browser sitemap crawl...")
        print(
            "[*] A browser window will open. If Cloudflare blocks a page, solve"
            " it manually."
        )

        while queue:
            current_url = clean_url_string(queue.pop(0))
            if current_url in visited_sitemaps:
                continue
            visited_sitemaps.add(current_url)

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
                    time.sleep(2)  # Wait a moment for the page to stabilize

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

            for a in anchor_tags:
                href = a["href"]
                if not href:
                    continue

                full_url = clean_url_string(urljoin(current_url, href))
                parsed_url = urlparse(full_url)
                netloc = parsed_url.netloc.lower()

                # Case 1: Sub-sitemap pagination/range link (Level 2 or Level 3)
                if "community.fandom.com" in netloc:
                    if any(
                        kw in parsed_url.path or kw in parsed_url.query
                        for kw in ["Sitemap", "level=", "from=", "to="]
                    ):
                        if not any(
                            x in full_url for x in ["auth.fandom.com", "signin", "register"]
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
                            page_wikis += 1

            if page_wikis > 0:
                print(
                    f"[+] Leaf page reached! Found {
                        page_wikis} new wikis here | Total"
                    f" unique: {len(seen_wikis)}"
                )
            else:
                print(
                    f"[-] Directory range page | Added {
                        sub_sitemaps_found} sub-ranges"
                    f" to queue | Queue size: {len(queue)}"
                )

            # Polite random delay between requests
            time.sleep(random.uniform(1.0, 2.0))

        browser.close()

    # Format collected subdomains into Special:Statistics URLs alphabetically
    output_urls = [
        f"https://{subdomain}.fandom.com/wiki/Special:Statistics"
        for subdomain in sorted(seen_wikis)
    ]
    return output_urls


if __name__ == "__main__":
    start_time = time.time()
    stats_links = crawl_fandom_sitemap()
    elapsed = time.time() - start_time

    print(f"\n[+] Extraction complete in {elapsed:.2f} seconds!")
    print(f"[+] Total unique wikis found: {len(stats_links)}")

    output_filename = "fandom_statistics_urls.txt"
    with open(output_filename, "w", encoding="utf-8") as f:
        for url in stats_links:
            f.write(url + "\n")

    print(f"[+] Saved output list to {output_filename}")
