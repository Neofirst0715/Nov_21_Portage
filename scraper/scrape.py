#!/usr/bin/env python3
"""Portage scraper: pull student / co-op postings from employer job boards into docs/jobs.json.

Standard library only, so GitHub Actions needs no pip install.
Run from the repo root:  python scraper/scrape.py
"""
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ROOT / "scraper" / "sources.json"
OUT = ROOT / "docs" / "jobs.json"
UA = "portage-coop-board/0.1 (personal student project)"
TODAY = dt.date.today()
NOW = dt.datetime.now(dt.timezone.utc)
UTC_DAY = NOW.strftime("%Y-%m-%d")
FORCE = os.environ.get("PORTAGE_FORCE", "").strip() == "1"  # manual runs ignore every_hours
HEARTBEAT_HOURS = 6  # rewrite jobs.json at least this often even when nothing changed

# ---------- http ----------

class QuotaExceeded(Exception):
    pass


CALLS = 0  # API requests made by the adapter currently running


def http(url, data=None, timeout=30, headers=None):
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    hdrs.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def api(url, data=None, headers=None):
    """http() for metered APIs: counts the request and turns 'out of quota' answers into QuotaExceeded."""
    global CALLS
    CALLS += 1
    try:
        return http(url, data, headers=headers)
    except urllib.error.HTTPError as e:
        if e.code in (402, 429):
            raise QuotaExceeded("HTTP %d" % e.code)
        raise


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


class NotConfigured(Exception):
    pass


def adzuna(src, keep):
    """Adzuna aggregator (free key from developer.adzuna.com). Keys come from the environment, never from files."""
    app_id, app_key = os.environ.get("ADZUNA_APP_ID", "").strip(), os.environ.get("ADZUNA_APP_KEY", "").strip()
    if not app_id or not app_key:
        raise NotConfigured("set ADZUNA_APP_ID and ADZUNA_APP_KEY")
    country = src.get("country", "ca")
    seen, failures = {}, []
    for q in src["queries"]:
        q = dict(q)
        pages = q.pop("pages", 1)
        try:
            for page in range(1, pages + 1):
                params = {"app_id": app_id, "app_key": app_key, "results_per_page": 50, "sort_by": "date",
                          "max_days_old": src.get("max_days_old", 30), "content-type": "application/json"}
                params.update(q)
                url = "https://api.adzuna.com/v1/api/jobs/%s/search/%d?%s" % (country, page, urllib.parse.urlencode(params))
                results = api(url).get("results", [])
                for r in results:
                    seen.setdefault(str(r.get("id")), r)
                if len(results) < 50:
                    break
        except QuotaExceeded:
            raise
        except Exception as e:  # one bad query shouldn't lose the others
            failures.append(str(e))
            print("  adzuna query %s failed: %s" % (q, scrub(str(e))), file=sys.stderr)
    if failures and len(failures) == len(src["queries"]):
        raise RuntimeError(failures[0])
    out = []
    for key, r in seen.items():
        title = strip_html(r.get("title", ""))
        loc = (r.get("location") or {}).get("display_name", "")
        if not r.get("redirect_url") or not keep(title, loc + ", Canada"):
            continue
        out.append({"key": key, "title": title, "location": loc, "url": r["redirect_url"],
                    "posted": (r.get("created") or "")[:10] or None, "text": strip_html(r.get("description", "")),
                    "company": strip_html((r.get("company") or {}).get("display_name", "")) or "Employer not stated"})
    return out


def jsearch(src, keep):
    """JSearch (Google for Jobs results). Key in JSEARCH_API_KEY. via = "openwebninja" (default) or "rapidapi"."""
    key = os.environ.get("JSEARCH_API_KEY", "").strip()
    if not key:
        raise NotConfigured("set JSEARCH_API_KEY")
    if src.get("via", "openwebninja") == "rapidapi":
        base, headers = "https://jsearch.p.rapidapi.com/search", {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    else:
        base, headers = "https://api.openwebninja.com/jsearch/search", {"x-api-key": key}
    seen, failures = {}, []
    for q in src["queries"]:
        params = {"query": q, "page": 1, "num_pages": 1, "country": src.get("country", "ca"),
                  "date_posted": src.get("date_posted", "month")}
        try:
            for r in api(base + "?" + urllib.parse.urlencode(params), headers=headers).get("data") or []:
                seen.setdefault(str(r.get("job_id")), r)
        except QuotaExceeded:
            raise
        except Exception as e:
            failures.append(str(e))
            print("  jsearch query %r failed: %s" % (q, scrub(str(e))), file=sys.stderr)
    if failures and len(failures) == len(src["queries"]):
        raise RuntimeError(failures[0])
    out = []
    for key_, r in seen.items():
        title = (r.get("job_title") or "").strip()
        loc = ", ".join(x for x in (r.get("job_city"), r.get("job_state"), r.get("job_country")) if x)
        if (r.get("job_country") or "").upper() in ("CA", "CANADA") and "canada" not in loc.lower():
            loc += ", Canada"
        url = r.get("job_apply_link") or r.get("job_google_link")
        if not url or not keep(title, loc):
            continue
        text = strip_html(r.get("job_description", "")) + (" remote" if r.get("job_is_remote") else "")
        out.append({"key": key_, "title": title, "location": loc.replace(", CA", "").replace(", Canada", "") or "Canada",
                    "url": url, "posted": (r.get("job_posted_at_datetime_utc") or "")[:10] or None, "text": text,
                    "company": (r.get("employer_name") or "").strip() or "Employer not stated"})
    return out


def jooble(src, keep):
    """Jooble aggregator. Key in JOOBLE_API_KEY; a key is issued for one country (request it on that country's Jooble site)."""
    key = os.environ.get("JOOBLE_API_KEY", "").strip()
    if not key:
        raise NotConfigured("set JOOBLE_API_KEY")
    hosts = src.get("hosts", ["jooble.org", "ca.jooble.org"])
    seen, failures, host = {}, [], None
    for q in src["queries"]:
        body = {"keywords": q["keywords"], "location": q.get("location", "Canada"), "page": 1,
                "ResultOnPage": src.get("per_page", 50)}
        try:
            data = None
            for h in ([host] if host else hosts):
                try:
                    data = api("https://%s/api/%s" % (h, key), body)
                    host = h  # remember which host accepts this key
                    break
                except urllib.error.HTTPError as e:
                    if e.code not in (403, 404) or h == hosts[-1] or host:
                        raise
            for r in (data or {}).get("jobs") or []:
                seen.setdefault(str(r.get("id") or r.get("link")), r)
        except QuotaExceeded:
            raise
        except Exception as e:
            failures.append(str(e))
            print("  jooble query %r failed: %s" % (q, scrub(str(e))), file=sys.stderr)
    if failures and len(failures) == len(src["queries"]):
        raise RuntimeError(failures[0])
    out = []
    for key_, r in seen.items():
        title, loc = strip_html(r.get("title", "")), strip_html(r.get("location", ""))
        if not r.get("link") or not keep(title, loc + ", Canada"):
            continue
        out.append({"key": key_, "title": title, "location": loc or "Canada", "url": r["link"],
                    "posted": (r.get("updated") or "")[:10] or None,
                    "text": strip_html(r.get("snippet", "")) + " " + (r.get("type") or ""),
                    "company": strip_html(r.get("company", "")) or "Employer not stated"})
    return out


def simplify(src, keep):
    """SimplifyJobs community internship list on GitHub: one public JSON file, no key and no quota."""
    url = src.get("url", "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json")
    out = []
    for r in http(url, timeout=60):
        if r.get("active") is False or r.get("is_visible") is False or not r.get("url"):
            continue
        locs = [l for l in (r.get("locations") or []) if parse_location(l)[0]]
        title = (r.get("title") or "").strip()
        if not locs or not title:
            continue
        ts = r.get("date_posted")
        posted = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat() if isinstance(ts, (int, float)) else None
        terms = " ".join(r.get("terms") or [])
        out.append({"key": str(r.get("id") or r["url"]), "title": title, "location": locs[0], "url": r["url"],
                    "posted": posted, "text": "%s %s %s" % (title, terms, r.get("category") or ""),
                    "company": (r.get("company_name") or "").strip() or "Employer not stated"})
    return out


ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "workday": workday, "adzuna": adzuna,
            "jsearch": jsearch, "jooble": jooble, "simplify": simplify}

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
        "id": jid, "source": src["name"], "company": raw.get("company") or src.get("company", src["name"]),
        "via": src["name"] if raw.get("company") else "",
        "title": raw["title"], "location": raw["location"], "city": city, "prov": prov, "url": raw["url"],
        "posted": raw.get("posted"), "first_seen": old.get("first_seen") or TODAY.isoformat(),
        "terms": find_terms(raw["title"], text), "months": find_months(raw["title"]),
        "mode": find_mode(text, raw["location"]), "cat": find_cat(raw["title"]),
        "skills": find_skills(text), "snippet": text[:260], "last_seen": TODAY.isoformat(),
    }


def scrub(msg):
    """Never let an API key reach logs or jobs.json."""
    for name in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JSEARCH_API_KEY", "JOOBLE_API_KEY"):
        val = os.environ.get(name, "").strip()
        if val:
            msg = msg.replace(val, "***")
    msg = re.sub(r"jooble\.org/api/[^\s/?]+", "jooble.org/api/***", msg)
    return re.sub(r"app_(id|key)=[^&\s]+", r"app_\1=***", msg)[:200]


def hours_since(iso):
    try:
        return (NOW - dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)).total_seconds() / 3600
    except (TypeError, ValueError):
        return 1e9


def dedupe(jobs):
    """Same employer + title + province counts once; a direct employer source beats an aggregator copy."""
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()
    best = {}
    for j in jobs:
        key = (norm(j["company"]), norm(j["title"]), j.get("prov", ""))
        cur = best.get(key)
        if cur is None or (cur.get("via") and not j.get("via")):
            best[key] = j
    return list(best.values())


def budget_left(src, usage):
    """Requests this source may still make, or None when it has no budget."""
    b = src.get("budget")
    if not b:
        return None
    u = usage.get(src["name"], {})
    if "total" in b:
        return max(0, b["total"] - u.get("total_used", 0))
    if "per_day" in b:
        return max(0, b["per_day"] - (u.get("used_today", 0) if u.get("day") == UTC_DAY else 0))
    month = TODAY.strftime("%Y-%m")
    return max(0, b["per_month"] - (u.get("used", 0) if u.get("month") == month else 0))


def spend(src, usage, calls, exhausted=False):
    u = usage.setdefault(src["name"], {})
    month = TODAY.strftime("%Y-%m")
    if u.get("month") != month:
        u["month"], u["used"] = month, 0
    if u.get("day") != UTC_DAY:
        u["day"], u["used_today"] = UTC_DAY, 0
    u["used"] += calls
    u["used_today"] += calls
    u["total_used"] = u.get("total_used", 0) + calls
    b = src.get("budget") or {}
    if exhausted:  # the service itself said no: treat the allowance as gone
        if "total" in b:
            u["total_used"] = max(u["total_used"], b["total"])
        elif "per_day" in b:
            u["used_today"] = max(u["used_today"], b["per_day"])
        elif "per_month" in b:
            u["used"] = max(u["used"], b["per_month"])


def cost(src):
    qs = src.get("queries") or []
    return sum(q.get("pages", 1) if isinstance(q, dict) else 1 for q in qs) or 1


def main():
    global CALLS
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))["sources"]
    prev_doc = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"jobs": []}
    prev = {j["id"]: j for j in prev_doc.get("jobs", [])}
    prev_report = {r["name"]: r for r in prev_doc.get("sources", [])}
    usage = json.loads(json.dumps(prev_doc.get("usage", {})))
    stamp = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    jobs, report, ok_count, must_write, served = [], [], 0, False, set()

    def carried(src):
        """Postings we already had from this source; aggregator ones expire after keep_days."""
        kept = [j for j in prev.values() if j.get("source") == src["name"]]
        days = src.get("keep_days", 0)
        if days:
            cutoff = (TODAY - dt.timedelta(days=days)).isoformat()
            kept = [j for j in kept if (j.get("last_seen") or "") >= cutoff]
        return kept

    for src in sources:
        if src.get("enabled") is False:
            continue
        name, group = src["name"], src.get("group")
        kept, last, every = carried(src), prev_report.get(name, {}), src.get("every_hours", 1)
        base = {"name": name, "fetched": last.get("fetched"), "every_hours": every}
        left = budget_left(src, usage)
        if left is not None:
            base["left"] = left
        if group and group in served:  # an earlier source in this group already answered: stay on standby
            jobs += kept
            report.append(dict(base, ok=True, count=len(kept), note="standby"))
            ok_count += 1
            continue
        if not FORCE and hours_since(last.get("fetched")) < every - 0.25:
            jobs += kept
            report.append(last)
            if last.get("ok"):
                ok_count += 1
                served.add(group)
            print("skip %-28s next fetch in %.1f h" % (name, every - hours_since(last.get("fetched"))))
            continue
        if left is not None and left < cost(src):
            jobs += kept
            report.append(dict(base, ok=False, count=len(kept), error="quota used up"))
            print("skip %-28s quota used up (%d left, needs %d)" % (name, left, cost(src)))
            continue
        CALLS = 0
        try:
            got = [normalise(src, r, prev) for r in ADAPTERS[src["type"]](src, make_keep(src))]
            ids = {j["id"] for j in got}
            got += [j for j in kept if j["id"] not in ids] if src.get("keep_days") else []
            jobs += got
            ok_count += 1
            served.add(group)
            report.append(dict(base, ok=True, count=len(got), fetched=stamp))
            print("ok   %-28s %d postings, %d requests" % (name, len(got), CALLS))
        except NotConfigured as e:
            report.append({"name": name, "ok": False, "count": 0, "error": "not configured: %s" % e})
            print("skip %-28s not configured (%s)" % (name, e))
        except QuotaExceeded as e:
            jobs += kept
            spend(src, usage, 0, exhausted=True)
            report.append(dict(base, ok=False, count=len(kept), error="quota used up", fetched=stamp))
            print("FAIL %-28s out of quota (%s), trying the next source" % (name, e), file=sys.stderr)
        except Exception as e:
            # keep what we had for this source so one outage doesn't empty the board
            jobs += kept
            report.append(dict(base, ok=False, count=len(kept), error=scrub(str(e)), fetched=stamp))
            print("FAIL %-28s %s" % (name, scrub(str(e))), file=sys.stderr)
        if CALLS and src.get("budget"):
            spend(src, usage, CALLS)
            report[-1]["left"] = budget_left(src, usage)
            must_write = True  # usage must be saved or the budget would be forgotten
        must_write = must_write or (every > 1 and report[-1].get("fetched") == stamp)
    if ok_count == 0:
        print("No source succeeded; leaving docs/jobs.json untouched.", file=sys.stderr)
        return 1
    jobs = dedupe(jobs)
    jobs.sort(key=lambda j: (j.get("posted") or j["first_seen"], j["title"]), reverse=True)
    for r in report:  # counts after de-duplication
        r["count"] = sum(1 for j in jobs if j["source"] == r["name"])
    strip = lambda js: [{k: v for k, v in j.items() if k != "last_seen"} for j in js]
    sig = lambda rs: [(r["name"], r["ok"], r.get("note", ""), r.get("error", "")[:14]) for r in rs]
    changed = strip(jobs) != strip(prev_doc.get("jobs", [])) or sig(report) != sig(prev_doc.get("sources", []))
    if not changed and not must_write and hours_since(prev_doc.get("updated")) < HEARTBEAT_HOURS:
        print("no change in %d postings; file left as is (keeps deploys down)" % len(jobs))
        return 0
    doc = {"updated": stamp, "sources": report, "usage": usage, "jobs": jobs}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %d postings to %s" % (len(jobs), OUT.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
