"""
Interactive Notion Second Brain Setup, Verification & Test Helper.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure UTF-8 console output on Windows
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.config import load_settings
from src.publishers.notion_publisher import NotionPublisher


def format_guidance() -> str:
    return """
========================================================================
🧭 HOW TO CONNECT NOTION TO YOUR SECOND BRAIN:
========================================================================
1. CREATE AN INTEGRATION:
   - Go to: https://www.notion.so/profile/integrations
   - Click '+ New integration'.
   - Name it (e.g., 'Second Brain AI Pipeline').
   - Select your workspace.
   - Capabilities: Read, Update, and Insert content.
   - Click 'Save' and copy the 'Internal Integration Secret' (starts with 'ntn_' or 'secret_').

2. SET YOUR API KEY:
   - Add your key to .env:
     NOTION_API_KEY=ntn_xxxx...

3. SHARE YOUR NOTION PAGE OR DATABASE (CRITICAL!):
   - In Notion, open the Page or Database you want to use.
   - Click the '...' menu (top right) -> 'Connections' -> 'Connect to' -> Select your integration.
   - (Without this step, Notion returns an ObjectNotFound error!)

4. EXTRACT THE DATABASE ID OR PAGE ID:
   - Notion Database URL format:
     https://www.notion.so/workspace/<DATABASE_ID>?v=...
     (The 32-character string before '?v=' is your NOTION_DATABASE_ID)
   - Add it to .env:
     NOTION_DATABASE_ID=<DATABASE_ID>
     NOTION_SYNC_ENABLED=true
========================================================================
"""


def verify_connection() -> int:
    root = Path(__file__).resolve().parent
    settings = load_settings(root)

    print("\n🔍 Checking Notion Configuration...")

    if not settings.notion_api_key:
        print("\n❌ NOTION_API_KEY is not set in .env")
        print(format_guidance())
        return 1

    try:
        from notion_client import Client
        client = Client(auth=settings.notion_api_key)
    except ImportError:
        print("\n❌ 'notion-client' is not installed. Run: pip install notion-client")
        return 1

    # 1. Test Bot Authentication
    try:
        bot_user = client.users.me()
        bot_name = bot_user.get("name", "Unknown Bot")
        bot_id = bot_user.get("id", "")
        print(f"✅ Notion API Authentication: SUCCESS!")
        print(f"   Connected Bot / Integration: '{bot_name}' (ID: {bot_id})")
    except Exception as exc:
        print(f"\n❌ Notion API Authentication Failed: {exc}")
        print("Please verify your NOTION_API_KEY in .env.")
        return 1

    publisher = NotionPublisher(settings)

    # 2. Check Notion Database
    if settings.notion_database_id:
        db_id = settings.notion_database_id.replace("-", "")
        print(f"\n📂 Checking Database: {db_id}")
        try:
            db_meta = client.databases.retrieve(database_id=db_id)
            db_title_arr = db_meta.get("title", [])
            db_title = "".join(t.get("plain_text", "") for t in db_title_arr) or "Untitled Database"
            print(f"   Database Name: '{db_title}'")

            props = db_meta.get("properties", {})
            if not props and hasattr(client, "data_sources"):
                ds_list = db_meta.get("data_sources", [])
                if ds_list:
                    ds_id = ds_list[0].get("id")
                    ds_meta = client.data_sources.retrieve(data_source_id=ds_id)
                    props = ds_meta.get("properties", {})

            print("   Detected Properties:")
            for p_name, p_info in props.items():
                print(f"     - '{p_name}' ({p_info.get('type')})")

            # Check required properties
            has_url = any(k.lower() in ("url", "link", "video", "video url") for k in props)
            has_status = any(k.lower() == "status" for k in props)
            has_mode = any(k.lower() in ("mode", "type") for k in props)
            has_lang = any(k.lower() in ("language", "lang", "target language", "target_language") for k in props)

            print("\n   Property Compatibility Check:")
            print(f"     - Title/Name : ✅ Found ('{list(props.keys())[0] if props else 'Name'}')")
            print(f"     - URL/Link   : {'✅ Found' if has_url else '⚠️ Missing (Add a URL or Text property named URL)'}")
            print(f"     - Status     : {'✅ Found' if has_status else 'ℹ️ Optional (Add a Status or Select property named Status)'}")
            print(f"     - Mode       : {'✅ Found' if has_mode else 'ℹ️ Optional (Add a Select property named Mode with Summarize/Translate)'}")
            print(f"     - Language   : {'✅ Found' if has_lang else 'ℹ️ Optional (Add a Select property named Language with English/Tamil)'}")

            # Query pending links
            pending = publisher._fetch_from_database(db_id)
            print(f"\n   📋 Pending Queue Check: Found {len(pending)} item(s) ready to process.")
            for idx, item in enumerate(pending, 1):
                lang_disp = item.get("language", "auto").upper()
                print(f"      {idx}. [{item.get('mode', 'auto').upper()} / {lang_disp}] {item.get('title', 'No Title')} -> {item.get('url')}")


        except Exception as exc:
            err_msg = str(exc)
            print(f"   ❌ Failed to access database {db_id}: {exc}")
            if "Could not find" in err_msg or "object_not_found" in err_msg:
                print("\n   ⚠️  CRITICAL FIX: Your integration does not have access to this database!")
                print("   👉 Open the database in Notion -> Click '...' -> 'Connections' -> Select your bot.")
            return 1

    # 3. Check Notion Page
    if settings.notion_page_id:
        page_id = settings.notion_page_id.replace("-", "")
        print(f"\n📄 Checking Notion Page: {page_id}")
        try:
            page_meta = client.pages.retrieve(page_id=page_id)
            print(f"   Page Access: ✅ Verified")
            pending = publisher._fetch_from_page(page_id)
            print(f"   📋 Pending Links Found in Page: {len(pending)} item(s)")
            for idx, item in enumerate(pending, 1):
                print(f"      {idx}. [{item.get('mode', 'auto').upper()}] {item.get('title', 'No Title')} -> {item.get('url')}")
        except Exception as exc:
            print(f"   ❌ Failed to access page {page_id}: {exc}")
            print("   👉 Make sure to share the page with your Notion Integration via '...' -> 'Connections'.")
            return 1

    if not settings.notion_database_id and not settings.notion_page_id:
        print("\n⚠️ Neither NOTION_DATABASE_ID nor NOTION_PAGE_ID is configured in .env.")
        print("Tip: You can auto-generate a complete Second Brain database inside any Notion page!")
        print("Run: py test_notion.py --create-database <PARENT_PAGE_ID>")

    print("\n" + "=" * 60)
    print("🎉 Notion Integration Verification Finished!")
    print("=" * 60)
    return 0


def create_database(parent_page_id: str) -> int:
    root = Path(__file__).resolve().parent
    settings = load_settings(root)

    if not settings.notion_api_key:
        print("\n❌ NOTION_API_KEY must be set in .env before creating a database.")
        print(format_guidance())
        return 1

    clean_parent_id = parent_page_id.replace("-", "").strip()

    try:
        from notion_client import Client
        client = Client(auth=settings.notion_api_key)
    except ImportError:
        print("\n❌ 'notion-client' is not installed.")
        return 1

    print(f"\n🛠️ Creating pre-configured Second Brain Database under parent page: {clean_parent_id}...")

    schema = {
        "parent": {"type": "page_id", "page_id": clean_parent_id},
        "title": [
            {
                "type": "text",
                "text": {"content": "🧠 Second Brain - Media & Audiobooks"},
            }
        ],
        "properties": {
            "Name": {"title": {}},
            "URL": {"url": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "To Process", "color": "default"},
                        {"name": "In Progress", "color": "yellow"},
                        {"name": "Summarized", "color": "green"},
                        {"name": "Published", "color": "blue"},
                    ]
                }
            },
            "Mode": {
                "select": {
                    "options": [
                        {"name": "Summarize", "color": "purple"},
                        {"name": "Translate", "color": "orange"},
                        {"name": "Auto", "color": "gray"},
                    ]
                }
            },
            "Language": {
                "select": {
                    "options": [
                        {"name": "English", "color": "blue"},
                        {"name": "Tamil", "color": "green"},
                        {"name": "Auto", "color": "gray"},
                    ]
                }
            },
        },
    }


    try:
        new_db = client.databases.create(**schema)
        new_id = new_db.get("id", "").replace("-", "")

        # In notion-client 3.x, ensure data_source properties are populated
        if hasattr(client, "data_sources"):
            try:
                db_info = client.databases.retrieve(database_id=new_id)
                ds_list = db_info.get("data_sources", [])
                if ds_list:
                    ds_id = ds_list[0].get("id")
                    client.data_sources.update(data_source_id=ds_id, properties=schema["properties"])
            except Exception:
                pass

        print("\n" + "=" * 65)
        print("🎉 SUCCESS! Second Brain Database Created in your Notion workspace!")
        print(f"   Database Name : 🧠 Second Brain - Media & Audiobooks")
        print(f"   Database ID   : {new_id}")
        print("=" * 65)

        # Update .env
        env_file = root / ".env"
        if env_file.exists():
            content = env_file.read_text(encoding="utf-8")
            if "NOTION_DATABASE_ID=" in content:
                import re
                content = re.sub(r"NOTION_DATABASE_ID=.*", f"NOTION_DATABASE_ID={new_id}", content)
                content = re.sub(r"NOTION_SYNC_ENABLED=.*", "NOTION_SYNC_ENABLED=true", content)
                env_file.write_text(content, encoding="utf-8")
                print(f"\n✅ Automatically updated .env with NOTION_DATABASE_ID={new_id}")
                print("   and set NOTION_SYNC_ENABLED=true")
            else:
                print(f"\n👉 Add this to your .env file:\nNOTION_DATABASE_ID={new_id}\nNOTION_SYNC_ENABLED=true")

        return 0

    except Exception as exc:
        print(f"\n❌ Error creating Notion database: {exc}")
        print("\nCommon reasons:")
        print("1. The Parent Page has NOT been shared with the integration.")
        print("   -> Open that page in Notion -> Click '...' -> 'Connections' -> Add your bot.")
        print("2. The Page ID is invalid.")
        return 1


def list_objects() -> int:
    root = Path(__file__).resolve().parent
    settings = load_settings(root)
    if not settings.notion_api_key:
        print("❌ NOTION_API_KEY is not set.")
        return 1
    from notion_client import Client
    client = Client(auth=settings.notion_api_key)
    me = client.users.me()
    print(f"🤖 Bot Connected: '{me.get('name')}' (ID: {me.get('id')})")
    res = client.search()
    results = res.get("results", [])
    print(f"\nTotal objects found accessible by bot: {len(results)}")
    if not results:
        print("  ℹ️ No pages or databases have been shared with this integration yet.")
        print("     To connect a page or database:")
        print("     1. Open the page/database in Notion.")
        print("     2. Click '...' (top right) -> 'Connections' -> Select your bot.")
        return 0

    for idx, r in enumerate(results, 1):
        obj_type = r.get("object")
        r_id = r.get("id")
        title = "Untitled"
        if obj_type == "page":
            props = r.get("properties", {})
            for k, v in props.items():
                if v.get("type") == "title":
                    title = "".join(t.get("plain_text", "") for t in v.get("title", []))
                    break
        elif obj_type == "database":
            t_arr = r.get("title", [])
            title = "".join(t.get("plain_text", "") for t in t_arr)
        url = r.get("url", "")
        print(f"  {idx}. [{obj_type.upper()}] \"{title}\"")
        print(f"     ID: {r_id}")
        if url:
            print(f"     URL: {url}")
    return 0


def delete_all_objects() -> int:
    root = Path(__file__).resolve().parent
    settings = load_settings(root)
    if not settings.notion_api_key:
        print("❌ NOTION_API_KEY is not set.")
        return 1
    from notion_client import Client
    client = Client(auth=settings.notion_api_key)
    res = client.search()
    results = res.get("results", [])
    if not results:
        print("ℹ️ Nothing to delete! No pages or databases are currently connected to this integration.")
        return 0

    print(f"⚠️ Found {len(results)} object(s) accessible by bot. Deleting / Archiving...")
    for r in results:
        obj_type = r.get("object")
        r_id = r.get("id")
        try:
            if obj_type == "page":
                client.pages.update(page_id=r_id, archived=True)
                print(f"  🗑️ Archived page: {r_id}")
            elif obj_type == "database":
                client.databases.update(database_id=r_id, archived=True)
                print(f"  🗑️ Archived database: {r_id}")
        except Exception as exc:
            print(f"  ❌ Failed to archive {obj_type} {r_id}: {exc}")
    print("\n✅ All connected objects have been archived. Ready to start from scratch!")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Notion Second Brain Verification & Setup Helper"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all pages and databases currently accessible by the integration bot",
    )
    parser.add_argument(
        "--delete-all",
        action="store_true",
        help="Archive/delete all pages and databases currently accessible by the integration bot to start fresh",
    )
    parser.add_argument(
        "--create-database",
        type=str,
        metavar="PARENT_PAGE_ID",
        help="Automatically creates a pre-formatted Second Brain Database inside the specified parent page ID",
    )
    parser.add_argument(
        "--help-setup",
        action="store_true",
        help="Print the complete step-by-step Notion integration setup guide",
    )
    args = parser.parse_args()

    if args.help_setup:
        print(format_guidance())
        return 0

    if args.list:
        return list_objects()

    if args.delete_all:
        return delete_all_objects()

    if args.create_database:
        return create_database(args.create_database)

    return verify_connection()


if __name__ == "__main__":
    raise SystemExit(main())

