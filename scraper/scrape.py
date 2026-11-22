#!/usr/bin/env python3
"""Portage scraper: pull student / co-op postings from employer job boards into docs/jobs.json.

Standard library only, so GitHub Actions needs no pip install.
Run from the repo root:  python scraper/scrape.py
"""
import datetime as dt
import hashlib
import html
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ROOT / "scraper" / "sources.json"
OUT = ROOT / "docs" / "jobs.json"
UA = "portage-coop-board/0.1 (personal student project)"
TODAY = dt.date.today()

# ---------- http ----------

def http(url, data=None, timeout=30):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

# ---------- classification ----------

STUDENT_RE = re.compile(r"\b(co-?op|intern|interns|internship|internships|student|students|stagiaire|summer analyst)\b", re.I)

PROVINCES = {
    "NS": ["nova scotia"], "NB": ["new brunswick"], "PE": ["prince edward island"],
    "NL": ["newfoundland"], "QC": ["quebec", "québec"], "ON": ["ontario"], "MB": ["manitoba"],
    "SK": ["saskatchewan"], "AB": ["alberta"], "BC": ["british columbia"],
    "YT": ["yukon"], "NT": ["northwest territories"], "NU": ["nunavut"],
}
CITY_PROV = {
    "halifax": "NS", "dartmouth": "NS", "bedford": "NS", "toronto": "ON", "ottawa": "ON", "waterloo": "ON",
    "kitchener": "ON", "mississauga": "ON", "montreal": "QC", "montréal": "QC", "vancouver": "BC",
    "victoria": "BC", "calgary": "AB", "edmonton": "AB", "winnipeg": "MB", "regina": "SK", "saskatoon": "SK",
    "fredericton": "NB", "moncton": "NB", "saint john": "NB", "charlottetown": "PE", "st. john's": "NL",
}


def parse_location(loc):
    """Return (is_canada, city, prov) from a free-text location."""
    loc = (loc or "").strip()
    low = loc.lower()
    prov = ""
    for code, names in PROVINCES.items():
        if any(n in low for n in names) or re.search(r"(^|[\s,(/-])%s($|[\s,)/-])" % code, loc):
            prov = code
            break
    if not prov:
        for city, code in CITY_PROV.items():
            if city in low:
                prov = code
                break
    city = re.split(r"[,/|;(]", loc)[0].strip() if loc else ""
    low_city = city.lower()
    if "remote" in low_city or "canada" in low_city or any(low_city in names for names in PROVINCES.values()):
        city = ""
    is_canada = bool(prov) or "canada" in low
    return is_canada, city, prov


SEASON = {"winter": "W", "hiver": "W", "summer": "S", "été": "S", "ete": "S", "spring": "S",
          "fall": "F", "automne": "F", "autumn": "F"}
MONTH_TERM = {"january": "W", "may": "S", "september": "F"}


def find_terms(title, text):
    for scope in (title, (text or "")[:800]):
        found = []
        low = scope.lower()
        pat = "|".join(SEASON)
        for m in re.finditer(r"\b(%s)\W+(?:[\w-]+\W+){0,3}?(20\d\d)\b" % pat, low):
            found.append(SEASON[m.group(1)] + m.group(2)[2:])
        for m in re.finditer(r"\b(20\d\d)\s*(%s)\b" % pat, low):
            found.append(SEASON[m.group(2)] + m.group(1)[2:])
        for m in re.finditer(r"\b(january|may|september)\s*(20\d\d)\b", low):
            found.append(MONTH_TERM[m.group(1)] + m.group(2)[2:])
        if found:
            return sorted(set(found), key=lambda t: (t[1:], "WSF".index(t[0])))
    return []


def find_months(title):
    m = re.search(r"((?:\d{1,2}\s*(?:,|/|-|&|or|to|and)?\s*)+)-?\s*months?\b", title, re.I)
    if not m:
        return []
    return sorted({int(n) for n in re.findall(r"\d{1,2}", m.group(1)) if int(n) in (4, 8, 12, 16)})


def find_mode(text, location):
    low = (location + " " + (text or "")[:3000]).lower()
    if "hybrid" in low:
        return "Hybrid"
    if re.search(r"\bremote\b", low):
        return "Remote"
    if re.search(r"\b(on-?site|in-?office|in person)\b", low):
        return "On-site"
    return ""


CATS = [
    ("data", r"\bdata\b|analytic|analyst|business intelligence|\bbi\b|machine learning|\bml\b|\bai\b|statistic|quantitative"),
    ("software", r"software|developer|devops|\bsre\b|programmer|\bqa\b|cyber|full.?stack|front.?end|back.?end|cloud|\bit\b|technology"),
    ("finance", r"financ|account|audit|assurance|\btax\b|banking|credit|investment|treasury|actuar|wealth"),
    ("marketing", r"marketing|communications|content|social media|brand|sales"),
    ("eng", r"engineer|mechanical|electrical|civil|naval|manufactur"),
    ("health", r"health|clinical|pharma|bio|nurs"),
]


def find_cat(title):
    low = title.lower()
    for cat, pat in CATS:
        if re.search(pat, low):
            return cat
    return "other"


SKILLS = ["Python", "SQL", "R", "Excel", "Power BI", "Tableau", "pandas", "dbt", "Airflow", "Snowflake",
          "Databricks", "Spark", "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Git", "Java", "C++", "C#",
          "JavaScript", "TypeScript", "React", "Node.js", "Machine Learning", "PyTorch", "TensorFlow",
          "Statistics", "ETL", "SAS", "VBA", "Looker", "ArcGIS", "Oracle", "Linux", "LLM", "APIs", "Power Automate"]
SKILL_RE = [(s, re.compile(r"(^|[^a-z0-9+#])%s($|[^a-z0-9+#])" % re.escape(s.lower()))) for s in SKILLS]


def find_skills(text):
    low = (text or "").lower()
    return [s for s, rx in SKILL_RE if rx.search(low)]

# ---------- adapters: each returns [{key,title,location,url,posted,text}] ----------

def greenhouse(src, keep):
    data = http("https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % src["board"])
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        if not keep(j.get("title", ""), loc):
            continue
        out.append({"key": str(j["id"]), "title": j["title"].strip(), "location": loc, "url": j["absolute_url"],
                    "posted": (j.get("first_published") or j.get("updated_at") or "")[:10] or None,
                    "text": strip_html(j.get("content", ""))})
    return out


def lever(src, keep):
    data = http("https://api.lever.co/v0/postings/%s?mode=json" % src["site"])
    out = []
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location", "")
        if not keep(j.get("text", ""), loc):
            continue
        ms = j.get("createdAt")
        posted = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat() if ms else None
        out.append({"key": j["id"], "title": j["text"].strip(), "location": loc, "url": j["hostedUrl"], "posted": posted,
                    "text": (j.get("descriptionPlain") or "") + " " + (j.get("workplaceType") or "")})
    return out


def _workday_posted(s):
    low = (s or "").lower()
    if "today" in low:
        return TODAY.isoformat()
    if "yesterday" in low:
        return (TODAY - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days?", low)
    return (TODAY - dt.timedelta(days=int(m.group(1)))).isoformat() if m else None


def workday(src, keep):
    host, tenant, site = src["host"], src["tenant"], src["site"]
    base = "https://%s/wday/cxs/%s/%s" % (host, tenant, site)
    seen, out = {}, []
    for term in src.get("search", ["intern", "co-op", "student"]):
        for page in range(src.get("max_pages", 5)):
            data = http(base + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": term})
            posts = data.get("jobPostings", [])
            for p in posts:
                if p.get("externalPath"):
                    seen.setdefault(p["externalPath"], p)
            if len(posts) < 20:
                break
    for path, p in seen.items():
        title, loc = p.get("title", ""), p.get("locationsText", "")
        multi = bool(re.match(r"\d+ locations", loc.lower()))
        if not keep(title, loc, location_unknown=multi):
            continue
        text = ""
        try:
            info = http(base + path).get("jobPostingInfo", {})
            text = strip_html(info.get("jobDescription", ""))
            if multi:
                locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
                canada = [l for l in locs if parse_location(l)[0]]
                if not canada:
                    continue
                loc = canada[0]
        except Exception as e:  # detail is optional; keep the listing
            print("  detail failed for %s: %s" % (path, e), file=sys.stderr)
            if multi:
                continue
        out.append({"key": path, "title": title.strip(), "location": loc,
                    "url": "https://%s/en-US/%s%s" % (host, site, path),
                    "posted": _workday_posted(p.get("postedOn")), "text": text})
    return out


ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "workday": workday}

# ---------- main ----------

def make_keep(src):
    students_only = src.get("students_only", True)

    def keep(title, location, location_unknown=False):
        if students_only and not STUDENT_RE.search(title or ""):
            return False
        return location_unknown or parse_location(location)[0]
    return keep


def normalise(src, raw, prev):
    jid = hashlib.sha1((src["name"] + "|" + raw["key"]).encode()).hexdigest()[:12]
    _, city, prov = parse_location(raw["location"])
    text = raw.get("text") or ""
    old = prev.get(jid, {})
    return {
        "id": jid, "source": src["name"], "company": src.get("company", src["name"]),
        "title": raw["title"], "location": raw["location"], "city": city, "prov": prov, "url": raw["url"],
        "posted": raw.get("posted"), "first_seen": old.get("first_seen") or TODAY.isoformat(),
        "terms": find_terms(raw["title"], text), "months": find_months(raw["title"]),
        "mode": find_mode(text, raw["location"]), "cat": find_cat(raw["title"]),
        "skills": find_skills(text), "snippet": text[:260],
    }


def main():
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))["sources"]
    prev_doc = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"jobs": []}
    prev = {j["id"]: j for j in prev_doc.get("jobs", [])}
    jobs, report, ok_count = [], [], 0
    for src in sources:
        if src.get("enabled") is False:
            continue
        try:
            raws = ADAPTERS[src["type"]](src, make_keep(src))
            got = [normalise(src, r, prev) for r in raws]
            jobs += got
            ok_count += 1
            report.append({"name": src["name"], "ok": True, "count": len(got)})
            print("ok   %-28s %d postings" % (src["name"], len(got)))
        except Exception as e:
            # keep what we had for this source so one outage doesn't empty the board
            kept = [j for j in prev.values() if j.get("source") == src["name"]]
            jobs += kept
            report.append({"name": src["name"], "ok": False, "count": len(kept), "error": str(e)[:200]})
            print("FAIL %-28s %s" % (src["name"], e), file=sys.stderr)
    if ok_count == 0:
        print("Every source failed; leaving docs/jobs.json untouched.", file=sys.stderr)
        return 1
    jobs.sort(key=lambda j: (j.get("posted") or j["first_seen"], j["title"]), reverse=True)
    doc = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": report, "jobs": jobs}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %d postings to %s" % (len(jobs), OUT.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
