import os
import shutil
import requests
import py7zr


def download_fandom_dumps(subdomains, output_dir="./dumps"):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs("temp_extracted", exist_ok=True)

    for sub in subdomains:
        print(f"\n========================================")
        print(f"Processing: {sub}.fandom.com")
        print(f"========================================")

        # 1. Query Fandom API to ensure we get the exact internal dbname
        api_url = f"https://{sub}.fandom.com/api.php"
        params = {
            "action": "query",
            "meta": "siteinfo",
            "siprop": "general",
            "format": "json"
        }

        dbname = sub  # fallback
        try:
            res = requests.get(api_url, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json()
                dbname = data["query"]["general"]["dbname"]
                print(f"  [+] Resolved DB name: '{dbname}'")
        except Exception as e:
            print(f"  [!] Warning: Could not query API for {
                  sub}, trying fallback dbname '{sub}'. Error: {e}")

        # 2. Construct S3 URL for current pages XML dump (.7z)
        first_letter = dbname[0]
        first_two = dbname[:2]
        s3_url = f"https://s3.amazonaws.com/wikia_xml_dumps/{
            first_letter}/{first_two}/{dbname}_pages_current.xml.7z"

        archive_path = f"{dbname}_pages_current.xml.7z"

        # 3. Download the archive file from S3
        print(f"  [->] Downloading dump from S3...")
        try:
            response = requests.get(s3_url, stream=True, timeout=30)
            if response.status_code != 200:
                print(f"  [X] Failed: Dump not found on S3 for '{
                      dbname}' (HTTP {response.status_code})")
                continue

            with open(archive_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        except Exception as e:
            print(f"  [X] Download error: {e}")
            continue

        # 4. Extract and rename to ./dumps/xxx.xml
        print(f"  [->] Extracting archive...")
        try:
            with py7zr.SevenZipFile(archive_path, mode='r') as archive:
                archive.extractall(path="temp_extracted")

            # Find the extracted XML file inside temp folder
            extracted_files = os.listdir("temp_extracted")
            xml_files = [f for f in extracted_files if f.endswith('.xml')]

            if xml_files:
                src_xml = os.path.join("temp_extracted", xml_files[0])
                dest_xml = os.path.join(output_dir, f"{sub}.xml")

                # Move/Rename to final destination
                if os.path.exists(dest_xml):
                    os.remove(dest_xml)
                shutil.move(src_xml, dest_xml)
                print(f"  [✓] Success! Saved to: {dest_xml}")
            else:
                print(
                    f"  [X] Extraction error: No XML file found inside archive.")

        except Exception as e:
            print(f"  [X] Extraction failed: {e}")

        # 5. Cleanup temp files for this iteration
        if os.path.exists(archive_path):
            os.remove(archive_path)
        if os.path.exists("temp_extracted"):
            shutil.rmtree("temp_extracted")
            os.makedirs("temp_extracted", exist_ok=True)

    # Final cleanup of temp directory
    if os.path.exists("temp_extracted"):
        shutil.rmtree("temp_extracted")
    print("\nAll tasks completed!")


# --- DEFINE YOUR TARGET SUBDOMAINS HERE ---
if __name__ == "__main__":
    with open("subdomains.txt", "r") as f:
        target_wikis = [line.strip() for line in f if line.strip()]

    download_fandom_dumps(target_wikis)
