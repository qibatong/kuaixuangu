
import sys; sys.path.insert(0,"/opt/kuaixuan/aipick/scripts")
import collector
rows = collector.fetch_from_kuaixuan("2026-08-27")
if rows:
    for r in rows:
        if str(r.get("code"))=="002418" or r.get("name")=="康盛股份":
            import json; print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
            break
    else:
        print("NOT FOUND in fetch_from_kuaixuan")
