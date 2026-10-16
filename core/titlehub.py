import uuid
import asyncio

def get_default_scid(title_id):
    """Returns the default SCID UUID for a title_id (0, title_id as u64)."""
    try:
        tid = int(title_id)
        return str(uuid.UUID(int=tid))
    except Exception:
        return str(title_id)

def is_xbox_console_title(devices, platform=""):
    """
    Returns True if title is strictly an Xbox One or Xbox Series game.
    Hides PC-only games and excludes Xbox 360 games entirely.
    """
    dev_lower = [str(d).lower() for d in devices]
    
    # Exclude any 360 devices
    if any("360" in d for d in dev_lower):
        return False

    # Check device list from Titlehub
    if dev_lower:
        is_console = any(any(k in d for k in ["xboxone", "xboxseries", "durango", "scarlett"]) for d in dev_lower)
        is_pc_only = all(d in ["pc", "win32", "windows", "windowsonecore"] for d in dev_lower)
        if is_console and not is_pc_only:
            return True
        if is_pc_only:
            return False

    # Check platform string from Achievements
    plat = str(platform).lower()
    if "360" in plat:
        return False
    if plat in ["xboxone", "xboxseries", "durango", "scarlett"]:
        return True
        
    return False

async def fetch_user_console_games(client, max_items=250, existing_cache=None, progress_callback=None):
    """
    Fetches user's played & owned Xbox One and Xbox Series games from Xbox Titlehub & Achievements.
    Filters out PC-only and Xbox 360 games. Reuses cached image URLs.
    """
    if progress_callback:
        progress_callback(15)

    cache_img_map = {}
    if existing_cache:
        for g in existing_cache:
            tid = g.get("title_id")
            img = g.get("display_image")
            if tid and img:
                cache_img_map[tid] = img

    games = []
    seen_tids = set()
    errors = []

    # 1. Query Titlehub for played/owned titles
    try:
        fields = "scid,detail,image,achievement,alternateTitleId"
        url = f"https://titlehub.xboxlive.com/users/xuid({client.xuid})/titles/titlehistory/decoration/{fields}"
        data = await client.get(url, params={"maxItems": max_items})
        
        for t in data.get("titles", []):
            tid = int(t.get("titleId", 0))
            if not tid or tid in seen_tids:
                continue
                
            media_type = str(t.get("mediaItemType", ""))
            if "360" in media_type or media_type in ["Xbox360Game", "XboxArcadeGame"]:
                continue
            if "360" in str(t.get("name", "")).lower():
                continue

            devices = t.get("devices", [])
            # Filter strictly for Xbox One and Xbox Series (hide PC and 360)
            if not is_xbox_console_title(devices):
                continue
                
            seen_tids.add(tid)
            scid = t.get("serviceConfigId") or get_default_scid(tid)
            pfn = t.get("pfn") or t.get("packageFamilyName") or ""
            
            # Extract optimal vertical box art / poster image
            images = t.get("images", [])
            img_map = {im.get("type"): im.get("url") for im in images if im.get("url")}
            
            # Prioritize standard 2:3 vertical box covers
            display_img = (
                img_map.get("Poster") or
                img_map.get("BoxArt") or
                img_map.get("BrandedKeyArt") or
                cache_img_map.get(tid) or
                t.get("displayImage", "") or
                img_map.get("Tile") or
                (images[0].get("url", "") if images else "")
            )

            if display_img and display_img.startswith("http://"):
                display_img = "https://" + display_img[7:]

            dev_names = []
            for d in devices:
                d_str = str(d)
                if "xboxone" in d_str.lower():
                    dev_names.append("Xbox One")
                elif "xboxseries" in d_str.lower() or "scarlett" in d_str.lower():
                    dev_names.append("Xbox Series X|S")
                elif "xbox" in d_str.lower() and "360" not in d_str.lower():
                    dev_names.append("Xbox Console")
            if not dev_names:
                dev_names = ["Xbox One"]

            games.append({
                "name": t.get("name", "Unknown Title"),
                "title_id": tid,
                "title_id_hex": f"0x{tid:08X}",
                "scid": scid,
                "pfn": pfn,
                "display_image": display_img,
                "devices": dev_names,
                "platform_badge": " / ".join(dict.fromkeys(dev_names)),
                "notes": f"Console game ({', '.join(dict.fromkeys(dev_names))})"
            })
    except Exception as e:
        print(f"[Titlehub] Error fetching title history: {e}")
        errors.append(e)

    if progress_callback:
        progress_callback(50)

    # 2. Query Achievements history for any additional Xbox One/Series titles
    try:
        url = f"https://achievements.xboxlive.com/users/xuid({client.xuid})/history/titles"
        data = await client.get(url, headers={"x-xbl-contract-version": "2"})
        for t in data.get("titles", []):
            tid = int(t.get("titleId", 0))
            if not tid or tid in seen_tids:
                continue

            # Natively filter out apps
            title_type = str(t.get("titleType", "")).lower()
            if "app" in title_type:
                continue

            platform = t.get("platform", "")
            if "360" in str(platform).lower() or "360" in str(t.get("name", "")).lower():
                continue

            if not is_xbox_console_title([], platform=platform):
                continue

            seen_tids.add(tid)
            scid = t.get("serviceConfigId") or get_default_scid(tid)

            plat_badge = "Xbox One"
            if "series" in str(platform).lower() or "scarlett" in str(platform).lower():
                plat_badge = "Xbox Series X|S"

            cached_img = cache_img_map.get(tid, "")
            if cached_img and cached_img.startswith("http://"):
                cached_img = "https://" + cached_img[7:]

            games.append({
                "name": t.get("name", "Unknown Title"),
                "title_id": tid,
                "title_id_hex": f"0x{tid:08X}",
                "scid": scid,
                "pfn": "",
                "display_image": cached_img,
                "devices": [plat_badge],
                "platform_badge": plat_badge,
                "notes": f"{t.get('earnedAchievements', 0)} achievements ({t.get('currentGamerscore', 0)} GS)"
            })
    except Exception as e:
        print(f"[Achievements] Error fetching titles: {e}")
        errors.append(e)

    if not games and errors:
        raise errors[0]

    if progress_callback:
        progress_callback(75)

    # 3. Resolve images for older console games (e.g. from Achievements history) that lack display_image
    missing_img_games = [g for g in games if not g.get("display_image")]
    if missing_img_games:
        total_missing = len(missing_img_games)
        resolved_count = 0
        sem = asyncio.Semaphore(4)

        async def resolve_image(g):
            nonlocal resolved_count
            async with sem:
                try:
                    tid = g["title_id"]
                    url = f"https://titlehub.xboxlive.com/users/xuid({client.xuid})/titles/titleid({tid})/decoration/image"
                    r = await client.get(url)
                    titles = r.get("titles", [])
                    if titles:
                        t = titles[0]
                        images = t.get("images", [])
                        img_map = {im.get("type"): im.get("url") for im in images if im.get("url")}
                        resolved_url = (
                            img_map.get("Poster") or
                            img_map.get("BoxArt") or
                            img_map.get("BrandedKeyArt") or
                            t.get("displayImage", "") or
                            img_map.get("Tile") or
                            (images[0].get("url", "") if images else "")
                        )
                        if resolved_url and resolved_url.startswith("http://"):
                            resolved_url = "https://" + resolved_url[7:]
                        g["display_image"] = resolved_url
                except Exception as e:
                    print(f"[Titlehub] Could not resolve image for {g.get('name')}: {e}")
                finally:
                    resolved_count += 1
                    if progress_callback and total_missing > 0:
                        pct = 75 + int((resolved_count / total_missing) * 20)
                        progress_callback(pct)

        await asyncio.gather(*[resolve_image(g) for g in missing_img_games])

    if progress_callback:
        progress_callback(98)

    return games
