import asyncio
import os
import aiohttp
from playwright.async_api import async_playwright

INPUT_FILE = "fandom_statistics_urls.txt"
OUTPUT_FILE = "fandom_dump_urls.txt"
PROGRESS_FILE = "processed_statistics_urls.txt"

CONCURRENCY_LIMIT = 50


async def get_cloudflare_cookies():
    """Opens a browser asynchronously to bypass Cloudflare and capture cookies."""
    print(
        "[*] Opening browser to establish session and bypass Cloudflare if"
        " needed..."
    )
    async with async_playwright() as p:
        # FIXED: Added 'await' here
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        try:
            await page.goto("https://community.fandom.com/wiki/Sitemap", timeout=60000)
            title = await page.title()
            content = await page.content()

            if (
                "Just a moment" in title
                or "Attention Required" in title
                or "cf-chl" in content
            ):
                print("\n" + "=" * 60)
                print("[!] CLOUDFLARE CHALLENGE DETECTED")
                print("[!] Please solve the challenge in the browser window.")
                print("[!] Once the sitemap loads, come back here and press ENTER.")
                print("=" * 60)
                input("Press [Enter] to continue...")

            cookies = await context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies}
            print(
                f"[+] Successfully captured {len(cookie_dict)} session cookies!")
            await browser.close()
            return cookie_dict
        except Exception as e:
            print(f"[!] Browser setup error: {e}")
            await browser.close()
            return {}


async def check_s3_fast(session, url, semaphore):
    """Checks S3 instantly via 1-byte range request."""
    async with semaphore:
        try:
            headers = {"Range": "bytes=0-0"}
            async with session.get(url, headers=headers, timeout=6) as resp:
                if resp.status in [200, 206]:
                    return True
                if resp.status in [403, 404]:
                    text = await resp.text()
                    if "DEEP_ARCHIVE" in text or "InvalidObjectState" in text:
                        return False
        except Exception:
            pass
        return False


async def fetch_api_wiki_id(session, api_url, semaphore):
    """Fetches dbname using captured session cookies to bypass Cloudflare."""
    async with semaphore:
        try:
            async with session.get(api_url, timeout=6) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("query", {}).get("general", {}).get("wikiid")
        except Exception:
            return None
    return None


async def process_wiki(
    session, stat_url, semaphore, out_file, progress_file, lock
):
    subdomain = stat_url.split("https://")[1].split(".fandom.com")[0].lower()
    if len(subdomain) < 2:
        return

    # 1. Fast Path: Standard S3 URL check
    c1, c2 = subdomain[0], subdomain[:2]
    s3_url = f"https://s3.amazonaws.com/wikia_xml_dumps/{
        c1}/{c2}/{subdomain}_pages_current.xml.7z"

    if await check_s3_fast(session, s3_url, semaphore):
        async with lock:
            out_file.write(s3_url + "\n")
            out_file.flush()
            progress_file.write(stat_url + "\n")
            progress_file.flush()
        return

    # 2. Fallback Path: Query API using session cookies for custom dbnames (e.g. rickandmorty292)
    api_url = f"https://{
        subdomain}.fandom.com/api.php?action=query&meta=siteinfo&siprop=general&format=json"
    wiki_id = await fetch_api_wiki_id(session, api_url, semaphore)

    if wiki_id and wiki_id != subdomain:
        wc1, wc2 = wiki_id[0], wiki_id[:2]
        s3_alt_url = f"https://s3.amazonaws.com/wikia_xml_dumps/{
            wc1}/{wc2}/{wiki_id}_pages_current.xml.7z"

        if await check_s3_fast(session, s3_alt_url, semaphore):
            async with lock:
                out_file.write(s3_alt_url + "\n")
                out_file.flush()
                progress_file.write(stat_url + "\n")
                progress_file.flush()
            return

    # Log progress so we don't re-check
    async with lock:
        progress_file.write(stat_url + "\n")
        progress_file.flush()


async def main():
    if not os.path.exists(INPUT_FILE):
        print(f"[!] Error: {INPUT_FILE} not found.")
        return

    # Get cookies asynchronously via Playwright
    cookies = await get_cloudflare_cookies()

    processed_set = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as pf:
            for line in pf:
                processed_set.add(line.strip())
        print(f"[*] Resuming: {len(processed_set):,} wikis already processed.")

    urls_to_process = []
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            url = line.strip()
            if url and url not in processed_set:
                urls_to_process.append(url)

    print(
        f"[*] Processing {len(urls_to_process):,} remaining wikis with full"
        " async speed..."
    )

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    lock = asyncio.Lock()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
    }

    async with aiohttp.ClientSession(
        headers=headers, cookies=cookies
    ) as session:
        with open(OUTPUT_FILE, "a", encoding="utf-8") as out_file, open(
            PROGRESS_FILE, "a", encoding="utf-8"
        ) as progress_file:

            tasks = [
                process_wiki(
                    session, url, semaphore, out_file, progress_file, lock
                )
                for url in urls_to_process
            ]

            completed = 0
            for future in asyncio.as_completed(tasks):
                await future
                completed += 1
                if completed % 1000 == 0 or completed == len(tasks):
                    print(
                        f"[+] Processed {completed:,} / {len(tasks):,} wikis...")

    print("[+] All done! Valid dump URLs successfully generated.")


if __name__ == "__main__":
    asyncio.run(main())
