import json, sys
p = "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/live-trader-dashboard/feature.json"
d = json.load(open(p))
for s in d["steps"]:
    if s["id"] == sys.argv[1]:
        s["passes"] = True
json.dump(d, open(p, "w"), ensure_ascii=False, indent=2)
open(p, "a").write("\n")
