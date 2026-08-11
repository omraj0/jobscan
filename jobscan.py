#!/usr/bin/env python3
"""
jobscan.py — pull job postings straight from company ATS public JSON feeds.

No API keys. No Apify credits. No browser. No proxies.
Supports: Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee,
          Workday (CXS), Oracle Cloud Recruiting (ORC).

Usage:
    pip install requests
    python jobscan.py --init                 # writes a starter boards.json
    python jobscan.py                        # runs it, writes jobs_YYYY-MM-DD.csv
    python jobscan.py --detect <careers-url> # tells you the ATS + config line to add

Then hand the CSV to Claude with your resume and ask for match scoring.
"""

import argparse
import csv
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("Run:  pip install requests")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT = 25
NOW = datetime.now(timezone.utc)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def iso(dt):
    if not dt:
        return ""
    if isinstance(dt, (int, float)):
        # Lever uses epoch milliseconds
        if dt > 1e11:
            dt = dt / 1000.0
        dt = datetime.fromtimestamp(dt, tz=timezone.utc)
    if isinstance(dt, str):
        s = dt.strip().replace("Z", "+00:00")
        for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
            try:
                dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_workday_posted(text):
    """Workday returns English like 'Posted 3 Days Ago'. Turn it into a date."""
    if not text:
        return ""
    t = text.lower()
    if "today" in t:
        return iso(NOW)
    if "yesterday" in t:
        return iso(NOW - timedelta(days=1))
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return iso(NOW - timedelta(days=int(m.group(1))))
    m = re.search(r"(\d+)\+?\s*month", t)
    if m:
        return iso(NOW - timedelta(days=30 * int(m.group(1))))
    return ""


def age_days(iso_str):
    if not iso_str:
        return None
    try:
        return (NOW - datetime.fromisoformat(iso_str)).days
    except ValueError:
        return None


def strip_html(s, limit=1200):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;?", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def row(company, source, title, location, url, posted="",
        employment_type="", department="", description=""):
    return {
        "company": company,
        "source": source,
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "employment_type": employment_type or "",
        "department": department or "",
        "posted_at": posted,
        "age_days": age_days(posted),
        "apply_url": url,
        "description": strip_html(description),
    }


def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------------
# fetchers — one per ATS
# ----------------------------------------------------------------------------

def fetch_greenhouse(cfg):
    slug, name = cfg["slug"], cfg.get("company", cfg["slug"])
    d = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        out.append(row(
            name, "greenhouse", j.get("title"),
            (j.get("location") or {}).get("name"),
            j.get("absolute_url"),
            # NB: Greenhouse only gives updated_at, which moves when a post is edited.
            iso(j.get("first_published") or j.get("updated_at")),
            description=j.get("content", ""),
        ))
    return out


def fetch_lever(cfg):
    slug, name = cfg["slug"], cfg.get("company", cfg["slug"])
    d = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in d:
        c = j.get("categories") or {}
        out.append(row(
            name, "lever", j.get("text"), c.get("location"),
            j.get("hostedUrl"), iso(j.get("createdAt")),
            employment_type=c.get("commitment", ""), department=c.get("team", ""),
            description=j.get("descriptionPlain", ""),
        ))
    return out


def fetch_ashby(cfg):
    slug, name = cfg["slug"], cfg.get("company", cfg["slug"])
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            "?includeCompensation=true")
    out = []
    for j in d.get("jobs", []):
        loc = j.get("location") or ""
        if j.get("isRemote"):
            loc = f"{loc} (Remote)".strip()
        out.append(row(
            name, "ashby", j.get("title"), loc, j.get("jobUrl"),
            iso(j.get("publishedAt")), employment_type=j.get("employmentType", ""),
            department=j.get("department", ""),
            description=j.get("descriptionPlain", ""),
        ))
    return out


def fetch_smartrecruiters(cfg):
    slug, name = cfg["slug"], cfg.get("company", cfg["slug"])
    out, offset = [], 0
    while True:
        d = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                f"?limit=100&offset={offset}")
        items = d.get("content", [])
        for j in items:
            loc = j.get("location") or {}
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            out.append(row(
                name, "smartrecruiters", j.get("name"),
                ", ".join(p for p in parts if p),
                f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                iso(j.get("releasedDate")),
                employment_type=(j.get("typeOfEmployment") or {}).get("label", ""),
                department=(j.get("department") or {}).get("label", ""),
            ))
        offset += 100
        if not items or offset >= d.get("totalFound", 0):
            break
    return out


def fetch_workable(cfg):
    slug, name = cfg["slug"], cfg.get("company", cfg["slug"])
    d = get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    out = []
    for j in d.get("jobs", []):
        parts = [j.get("city"), j.get("state"), j.get("country")]
        out.append(row(
            name, "workable", j.get("title"),
            ", ".join(p for p in parts if p),
            j.get("url") or j.get("application_url"),
            iso(j.get("published_on")),
            employment_type=j.get("employment_type", ""),
            department=j.get("department", ""),
            description=j.get("description", ""),
        ))
    return out


def fetch_recruitee(cfg):
    slug, name = cfg["slug"], cfg.get("company", cfg["slug"])
    d = get(f"https://{slug}.recruitee.com/api/offers/")
    out = []
    for j in d.get("offers", []):
        out.append(row(
            name, "recruitee", j.get("title"), j.get("location"),
            j.get("careers_url") or j.get("careers_apply_url"),
            iso(j.get("published_at")),
            employment_type=j.get("employment_type_code", ""),
            department=j.get("department", ""),
            description=j.get("description", ""),
        ))
    return out


def fetch_workday(cfg):
    """Undocumented but stable CXS endpoint every Workday careers site uses."""
    host = cfg["host"].replace("https://", "").rstrip("/")
    tenant, site = cfg["tenant"], cfg["site"]
    name = cfg.get("company", tenant)
    search = cfg.get("search", "")
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    out, offset = [], 0
    while offset < cfg.get("max", 200):
        r = requests.post(
            url, timeout=TIMEOUT,
            headers={**UA, "Content-Type": "application/json"},
            json={"appliedFacets": {}, "limit": 20, "offset": offset,
                  "searchText": search},
        )
        r.raise_for_status()
        d = r.json()
        posts = d.get("jobPostings", [])
        for j in posts:
            out.append(row(
                name, "workday", j.get("title"), j.get("locationsText"),
                f"https://{host}/en-US/{site}{j.get('externalPath', '')}",
                parse_workday_posted(j.get("postedOn")),
            ))
        offset += 20
        if len(posts) < 20 or offset >= d.get("total", 0):
            break
    return out


def fetch_oracle(cfg):
    """Oracle Cloud Recruiting (ORC) external careers site."""
    host = cfg["host"].replace("https://", "").rstrip("/")
    site_number = cfg["site_number"]
    name = cfg.get("company", host.split(".")[0])
    url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    headers = {
        **UA,
        "ora-irc-cx-userid": str(uuid.uuid4()),
        "ora-irc-language": "en",
        "Content-Type": "application/vnd.oracle.adf.resourceitem+json;charset=utf-8",
    }
    out, offset = [], 0
    while offset < cfg.get("max", 400):
        params = {
            "onlyData": "true",
            "expand": "requisitionList.workLocation,requisitionList.otherWorkLocations",
            "finder": f"findReqs;siteNumber={site_number},limit=100,offset={offset}",
        }
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        items = r.json().get("items", [])
        reqs = []
        for it in items:
            reqs.extend(it.get("requisitionList", []))
        for j in reqs:
            out.append(row(
                name, "oracle", j.get("Title"),
                j.get("PrimaryLocation") or "",
                f"https://{host}/hcmUI/CandidateExperience/en/sites/{site_number}"
                f"/job/{j.get('Id')}",
                iso(j.get("PostedDate")),
                employment_type=j.get("WorkerType", ""),
            ))
        offset += 100
        if len(reqs) < 100:
            break
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "recruitee": fetch_recruitee,
    "workday": fetch_workday,
    "oracle": fetch_oracle,
}


# ----------------------------------------------------------------------------
# ATS detection from a careers URL
# ----------------------------------------------------------------------------

DETECT = [
    (r"boards\.greenhouse\.io/([^/?#]+)", "greenhouse"),
    (r"job-boards\.greenhouse\.io/([^/?#]+)", "greenhouse"),
    (r"jobs\.lever\.co/([^/?#]+)", "lever"),
    (r"jobs\.ashbyhq\.com/([^/?#]+)", "ashby"),
    (r"jobs\.smartrecruiters\.com/([^/?#]+)", "smartrecruiters"),
    (r"apply\.workable\.com/([^/?#]+)", "workable"),
    (r"([^/.]+)\.recruitee\.com", "recruitee"),
]


def detect(url):
    for pat, ats in DETECT:
        m = re.search(pat, url)
        if m:
            return {"ats": ats, "slug": m.group(1), "company": m.group(1).title()}
    m = re.search(r"https?://([^/]*myworkdayjobs\.com)/(?:en-US/)?([^/?#]+)", url)
    if m:
        host = m.group(1)
        return {"ats": "workday", "host": host,
                "tenant": host.split(".")[0], "site": m.group(2),
                "company": host.split(".")[0].title()}
    m = re.search(r"https?://([^/]*oraclecloud\.com).*?sites/(CX_[0-9]+)", url)
    if m:
        return {"ats": "oracle", "host": m.group(1), "site_number": m.group(2),
                "company": m.group(1).split(".")[0]}
    return None


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

STARTER = {
    "keywords": ["data engineer", "analytics engineer", "data platform"],
    "exclude": ["intern", "internship", "principal", "director"],
    "locations": [],
    "max_age_days": 7,
    "boards": [
        {"ats": "greenhouse", "slug": "stripe", "company": "Stripe"},
        {"ats": "lever", "slug": "netflix", "company": "Netflix"},
        {"ats": "ashby", "slug": "ramp", "company": "Ramp"},
        {"ats": "smartrecruiters", "slug": "Visa", "company": "Visa"},
        {"ats": "workday", "host": "nvidia.wd5.myworkdayjobs.com",
         "tenant": "nvidia", "site": "NVIDIAExternalCareerSite", "company": "NVIDIA"},
    ],
}


def jobkey(j):
    return "|".join([j["company"].lower().strip(),
                     j["title"].lower().strip(),
                     j["location"].lower().strip()])


def matches(j, cfg):
    kws = [k.lower() for k in cfg.get("keywords", [])]
    exc = [k.lower() for k in cfg.get("exclude", [])]
    locs = [k.lower() for k in cfg.get("locations", [])]
    title = j["title"].lower()
    if kws and not any(k in title for k in kws):
        return False
    if any(k in title for k in exc):
        return False
    if locs and not any(k in j["location"].lower() for k in locs):
        return False
    max_age = cfg.get("max_age_days")
    if max_age and j["age_days"] is not None and j["age_days"] > max_age:
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="boards.json")
    ap.add_argument("--outdir", default="data",
                    help="where CSV, latest.md and seen.json are written")
    ap.add_argument("--no-seen", action="store_true",
                    help="ignore seen.json; treat every job as new")
    ap.add_argument("--init", action="store_true", help="write a starter config")
    ap.add_argument("--detect", metavar="URL", help="identify the ATS behind a careers URL")
    ap.add_argument("--no-filter", action="store_true", help="keep everything")
    args = ap.parse_args()

    if args.detect:
        d = detect(args.detect)
        print(json.dumps(d, indent=2) if d
              else "Unrecognised — likely iCIMS, Taleo, SuccessFactors or custom.")
        return

    if args.init:
        if os.path.exists(args.config):
            sys.exit(f"{args.config} already exists.")
        with open(args.config, "w") as f:
            json.dump(STARTER, f, indent=2)
        print(f"Wrote {args.config}. Edit the boards list, then re-run.")
        return

    if not os.path.exists(args.config):
        sys.exit(f"No {args.config}. Run:  python jobscan.py --init")

    cfg = json.load(open(args.config))
    os.makedirs(args.outdir, exist_ok=True)

    all_jobs, errors = [], []

    for b in cfg.get("boards", []):
        ats = b.get("ats")
        fn = FETCHERS.get(ats)
        label = b.get("company") or b.get("slug") or b.get("tenant") or ats
        if not fn:
            errors.append(f"{label}: unknown ats '{ats}'")
            continue
        try:
            jobs = fn(b)
            all_jobs.extend(jobs)
            print(f"  {label:<24} {ats:<16} {len(jobs):>4} postings")
        except Exception as e:
            errors.append(f"{label} ({ats}): {type(e).__name__} {e}")

    seen_now, deduped = set(), []
    for j in all_jobs:
        k = jobkey(j)
        if k not in seen_now:
            seen_now.add(k)
            deduped.append(j)

    kept = deduped if args.no_filter else [j for j in deduped if matches(j, cfg)]
    kept.sort(key=lambda j: (j["age_days"] is None, j["age_days"] or 0))

    # --- which of these have we never shown before? ---
    seen_path = os.path.join(args.outdir, "seen.json")
    previously = set()
    if os.path.exists(seen_path) and not args.no_seen:
        try:
            previously = set(json.load(open(seen_path)))
        except Exception:
            previously = set()
    fresh = [j for j in kept if jobkey(j) not in previously]

    # --- CSV of everything matched today ---
    csv_path = os.path.join(args.outdir, f"jobs_{NOW:%Y-%m-%d}.csv")
    cols = ["company", "title", "location", "employment_type", "department",
            "posted_at", "age_days", "source", "apply_url", "description"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(kept)

    # --- markdown of the NEW ones, renders on github.com and on your phone ---
    md_path = os.path.join(args.outdir, "latest.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Job scan \u2014 {NOW:%d %b %Y, %H:%M UTC}\n\n")
        f.write(f"**{len(fresh)} new** since last run \u00b7 "
                f"{len(kept)} matched today \u00b7 "
                f"{len(deduped)} unique fetched from "
                f"{len(cfg.get('boards', []))} boards\n\n")
        if fresh:
            f.write("| Company | Role | Location | Age | Apply |\n")
            f.write("|---|---|---|---|---|\n")
            for j in fresh[:80]:
                age = f"{j['age_days']}d" if j["age_days"] is not None else "?"
                t = j["title"].replace("|", "\\|")
                f.write(f"| {j['company']} | {t} | {j['location'] or '\u2014'} "
                        f"| {age} | [apply]({j['apply_url']}) |\n")
            if len(fresh) > 80:
                f.write(f"\n_...and {len(fresh) - 80} more in the CSV._\n")
        else:
            f.write("_Nothing new this run._\n")
        if errors:
            f.write("\n## Boards that errored\n\n")
            for e in errors:
                f.write(f"- `{e}`\n")

    if not args.no_seen:
        json.dump(sorted(previously | {jobkey(j) for j in kept}),
                  open(seen_path, "w"), indent=0)

    print(f"\n{len(all_jobs)} fetched -> {len(deduped)} unique -> "
          f"{len(kept)} matched -> {len(fresh)} NEW")
    print(f"  {csv_path}\n  {md_path}")
    if errors:
        print("\nProblems:")
        for e in errors:
            print("  -", e)


if __name__ == "__main__":
    main()
