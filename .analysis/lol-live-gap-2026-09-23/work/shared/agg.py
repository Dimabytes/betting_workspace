import re, sys, collections
for game in ("dota","lol"):
    rows=[]
    for line in open(f"{sys.argv[1]}/{game}_summary.txt"):
        m=re.search(r"fills=(\d+)\s+realized=(\S+) imv=(\S+) rebate=(\S+) net=(\S+).*joined=(\S+)", line)
        if not m: continue
        mm=re.search(r"mode=(\w+)", line); mode=mm.group(1) if mm else "?"
        tree=re.search(r"\[(\w+)\]", line).group(1)
        fills=int(m.group(1)); net=m.group(5); day=m.group(6)[:10]
        src = "grid" if line.startswith("grid-") else ("oddin" if line.startswith("oddin") else "steam")
        rows.append((day,mode,tree,fills,None if net=="n/a" else float(net),float(m.group(4)),src))
    agg=collections.defaultdict(lambda:[0,0,0,0.0,0,0,0.0])
    for day,mode,tree,fills,net,reb,src in rows:
        if mode!="live": continue
        a=agg[day]; a[0]+=1; a[1]+= fills>0; a[2]+=fills
        if net is not None and fills>0:
            a[3]+=net; a[4]+=1; a[5]+= net>0; a[6]+=reb
    print(game)
    print("day        maps traded fills   net     n_net wins rebate")
    for d in sorted(agg):
        if d<"2026-09-08": continue
        a=agg[d]; print(f"{d} {a[0]:4d} {a[1]:5d} {a[2]:5d} {a[3]:8.2f} {a[4]:4d} {a[5]:4d} {a[6]:6.2f}")
    # src split for last days
    s=collections.defaultdict(lambda:[0,0,0.0,0])
    for day,mode,tree,fills,net,reb,src in rows:
        if mode!="live" or day<"2026-09-18": continue
        x=s[src]; x[0]+=1; x[1]+=fills>0
        if net is not None and fills>0: x[2]+=net; x[3]+=net>0
    print("since 09-18 by source:", dict(s))
