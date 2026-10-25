import os
import json
import re
import hashlib
import urllib.parse
import xml.etree.ElementTree as ET
import mwparserfromhell

# --- DIRECTORY CONFIGURATION ---
DUMPS_DIR = "./dumps"
PARSED_DIR = "./parsed"


def get_fandom_image_url(filename, subdomain):
    if not filename:
        return None

    norm_name = filename.strip()
    if norm_name:
        norm_name = norm_name[0].upper() + norm_name[1:]
    norm_name = norm_name.replace(' ', '_')

    md5_hex = hashlib.md5(norm_name.encode('utf-8')).hexdigest()
    hash1 = md5_hex[0]
    hash2 = md5_hex[0:2]
    encoded_filename = urllib.parse.quote(norm_name)

    return f"https://static.wikia.nocookie.net/{subdomain}/images/{hash1}/{hash2}/{encoded_filename}/revision/latest?format=original"


def extract_image_filename(raw_wikitext):
    if not raw_wikitext:
        return None

    match = re.search(
        r'\|\s*image\s*=\s*([^|\n}]+)', raw_wikitext, re.IGNORECASE)
    if match:
        img = match.group(1).strip()
        img = re.sub(r'\[\[(?:File|Image):([^\]|]+)(?:\|.*)?\]\]',
                     r'\1', img, flags=re.IGNORECASE)
        if img and not img.lower().endswith(('.css', '.js', '.svg')):
            return img.strip()

    match_file = re.search(
        r'\[\[(?:File|Image):([^\]|]+)', raw_wikitext, re.IGNORECASE)
    if match_file:
        return match_file.group(1).strip()

    return None


def clean_fandom_text(raw_wikitext):
    if not raw_wikitext:
        return ""

    try:
        parsed = mwparserfromhell.parse(raw_wikitext)
        text = parsed.strip_code(collapse=True)
    except Exception:
        text = raw_wikitext

    # Post-processing cleanup
    text = re.sub(r'\{\|.*?\|\}', '', text, flags=re.DOTALL)
    text = re.sub(r'(?:Other\s+Languages|Languages|In\s+other\s+languages).*$',
                  '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'(?:Category:[^\n]+\n?)+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b[a-z]{2,3}:[A-Za-z0-9_\-\(\)]+\b', '', text)

    # Replace all newlines with a single space and normalize whitespace
    text = re.sub(r'[\r\n]+', ' ', text)
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


def process_dump_file(xml_file_path, output_ndjson_path, fandom_name):
    print(f"🔥 Processing [{fandom_name}] from {xml_file_path}...")

    wiki_base_url = f"https://{fandom_name}.fandom.com/wiki"
    processed_count = 0
    redirect_count = 0
    seen_ids = set()

    redirect_regex = re.compile(r'^\s*#?REDIRECT\b', re.IGNORECASE)

    with open(output_ndjson_path, 'w', encoding='utf-8') as out_f:
        context = ET.iterparse(xml_file_path, events=('end',))

        for event, elem in context:
            tag = elem.tag.split('}')[-1]

            if tag == 'page':
                title_elem = elem.find('{*}title')
                ns_elem = elem.find('{*}ns')
                text_elem = elem.find('.//{*}text')
                redirect_elem = elem.find('{*}redirect')

                if redirect_elem is not None:
                    redirect_count += 1
                    elem.clear()
                    continue

                if title_elem is not None and ns_elem is not None and text_elem is not None:
                    if ns_elem.text == '0':
                        title = title_elem.text or ""
                        raw_wikitext = text_elem.text or ""

                        if redirect_regex.match(raw_wikitext.strip()):
                            redirect_count += 1
                            elem.clear()
                            continue

                        # Include fandom name in ID to ensure uniqueness across multi-wiki stores
                        doc_id = title.replace(' ', '_') + f"_{fandom_name}"

                        if doc_id in seen_ids:
                            elem.clear()
                            continue

                        image_filename = extract_image_filename(raw_wikitext)
                        image_url = get_fandom_image_url(
                            image_filename, fandom_name) if image_filename else None

                        clean_text = clean_fandom_text(raw_wikitext)

                        if redirect_regex.match(clean_text.strip()):
                            redirect_count += 1
                            elem.clear()
                            continue

                        # 50-word lead for search/vector DB ingestion
                        words = clean_text.split()
                        lead_text = " ".join(words[:50])

                        page_url = f"{wiki_base_url}/{title.replace(' ', '_')}"

                        document = {
                            "id": doc_id,
                            "fandom": fandom_name,
                            "title": title,
                            "url": page_url,
                            "image": image_url,
                            "lead": lead_text,
                            "text": clean_text
                        }

                        out_f.write(json.dumps(
                            document, ensure_ascii=False) + '\n')
                        seen_ids.add(doc_id)
                        processed_count += 1

                        if processed_count % 5000 == 0:
                            print(f"[{fandom_name}] Processed {
                                  processed_count} unique articles...")

                elem.clear()

    print(f"✅ Finished [{fandom_name}]! Wrote {processed_count} documents to {
          output_ndjson_path} (Skipped {redirect_count} redirects).\n")


def main():
    # Ensure directories exist
    os.makedirs(DUMPS_DIR, exist_ok=True)
    os.makedirs(PARSED_DIR, exist_ok=True)

    xml_files = [f for f in os.listdir(DUMPS_DIR) if f.endswith('.xml')]

    if not xml_files:
        print(f"⚠️ No .xml files found inside '{
              DUMPS_DIR}/'. Please place your dump files there.")
        return

    print(f"📂 Found {len(xml_files)} dump file(s) to process.\n")

    for filename in xml_files:
        # Automatically extract fandom name (e.g. "fortnite_pages_current.xml" -> "fortnite")
        match = re.match(
            r'^(.+?)(?:_pages_current|_current|_dump)?\.xml$', filename, re.IGNORECASE)
        fandom_name = match.group(1).lower(
        ) if match else filename.replace('.xml', '').lower()

        xml_path = os.path.join(DUMPS_DIR, filename)
        output_path = os.path.join(PARSED_DIR, f"{fandom_name}.ndjson")

        process_dump_file(xml_path, output_path, fandom_name)

    print("🚀 All batch dumps processed successfully!")


if __name__ == "__main__":
    main()
