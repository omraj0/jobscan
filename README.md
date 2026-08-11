# jobscan

Daily job scan across ATS job boards. Runs itself on GitHub Actions every
morning at 08:00 IST, commits results back to this repo.

**Read your results here:** [`data/latest.md`](data/latest.md) — renders as a
table on github.com, works fine on a phone. Bookmark that URL.

---

## Setup (one time, ~10 minutes)

### 1. Create the repo

Go to <https://github.com/new>.

- **Name:** `jobscan`
- **Visibility:** **Private**
- Tick **"Add a README file"** — you need at least one commit before Actions
  will show up
- Create repository

### 2. Add the files

In the repo, use **Add file → Create new file** for each. Type the path
exactly as shown, including the slashes — GitHub creates the folders for you.

| Path to type | Paste in |
|---|---|
| `jobscan.py` | the jobscan.py file |
| `boards.json` | the boards.json file |
| `requirements.txt` | the requirements.txt file |
| `.github/workflows/jobscan.yml` | the jobscan.yml file |
| `README.md` | this file (replace what's there) |

Commit each one directly to `main`.

The `.github/workflows/` path matters — Actions only looks there. If you typo
it, the workflow silently never appears.

### 3. Allow the workflow to commit

**Settings → Actions → General → Workflow permissions** →
select **"Read and write permissions"** → **Save**.

Skip this and every run fails at the push step with a 403.

### 4. Run it once by hand

**Actions** tab → **Daily job scan** in the left sidebar →
**Run workflow** → **Run workflow**.

Give it 2–4 minutes. Then open `data/latest.md`.

If Actions says workflows are disabled, click the button to enable them —
GitHub does this on some new repos by default.

---

## Reading the output

Each run writes three things into `data/`:

| File | What it is |
|---|---|
| `latest.md` | Only jobs **new since the last run**, as a table. This is the one you read. |
| `jobs_YYYY-MM-DD.csv` | Everything matching today, full descriptions. Upload this to Claude. |
| `seen.json` | Bookkeeping so you never see the same posting twice. Don't edit. |

`latest.md` also lists any boards that errored at the bottom — worth glancing
at, since a slug that goes stale fails silently otherwise.

---

## Changing things

**Run at a different time.** Edit the cron in `.github/workflows/jobscan.yml`.
It's UTC, so subtract 5:30 from the IST time you want. 07:00 IST → `30 1 * * *`.

**Add a company.** Find its careers page, then locally or in any Python
environment:

```
python jobscan.py --detect https://boards.greenhouse.io/somecompany
```

Paste the JSON it prints into the `boards` array in `boards.json`.

**See everything again from scratch.** Delete `data/seen.json` and re-run.

**Widen or narrow results.** Edit `keywords`, `exclude`, `locations` and
`max_age_days` in `boards.json`. All matching is on the job title only.

---

## Daily loop

1. Open `data/latest.md` on your phone over coffee.
2. If anything looks worth a closer read, open the day's CSV in Claude and ask
   it to screen and rank against your profile.
3. Apply. Log what you applied to in your tracker sheet.

---

## Notes

- Free Actions tier is 2,000 minutes/month. This uses roughly 30. No cost.
- GitHub disables scheduled workflows in repos with no activity for 60 days.
  It emails you first; one manual run re-enables it.
- Keep the repo private. `boards.json` reveals your target companies and salary
  band inference.
- Nothing here scrapes LinkedIn or Naukri — those come through email alerts
  instead, deliberately.
