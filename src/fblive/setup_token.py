"""Interactive helper: short-lived user token -> never-expiring Page token, saved to .env."""
from __future__ import annotations

import getpass
from pathlib import Path

import requests


def write_env_values(path: Path, values: dict) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining and "=" in line and not line.lstrip().startswith("#"):
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def run_setup(env_path: Path, graph_version: str = "v26.0", base_url: str = "https://graph.facebook.com") -> int:
    base = f"{base_url.rstrip('/')}/{graph_version}"
    print("Get a short-lived USER token from the Graph API Explorer (developers.facebook.com/tools/explorer)")
    print("with permissions: pages_show_list, pages_read_engagement, pages_manage_posts, publish_video\n")
    app_id = input("App ID: ").strip()
    app_secret = getpass.getpass("App Secret (hidden): ").strip()
    short = getpass.getpass("Short-lived user token (hidden): ").strip()
    try:
        r = requests.get(f"{base}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
            "fb_exchange_token": short}, timeout=30)
        j = r.json()
        if "access_token" not in j:
            print("Token exchange failed:", j.get("error", {}).get("message", j))
            return 1
        long_user = j["access_token"]
        r = requests.get(f"{base}/me/accounts", params={"access_token": long_user, "limit": 100}, timeout=30)
        pages = r.json().get("data", [])
    except (requests.RequestException, ValueError) as e:
        print("Network/parse error:", str(e).replace(short, "***").replace(app_secret, "***"))
        return 1
    if not pages:
        print("No Pages found for this user (are you an admin of the Page? did you grant pages_show_list?)")
        return 1
    for i, pg in enumerate(pages, 1):
        print(f"  {i}. {pg.get('name')}  (id {pg.get('id')})")
    choice = 1 if len(pages) == 1 else int(input("Choose a Page number: ").strip() or "1")
    page = pages[choice - 1]
    write_env_values(env_path, {"FB_PAGE_ID": page["id"], "FB_PAGE_TOKEN": page["access_token"]})
    print(f"\nSaved FB_PAGE_ID and FB_PAGE_TOKEN for '{page.get('name')}' to {env_path}")
    print("Keep .env private - never commit it to GitHub.")
    return 0
