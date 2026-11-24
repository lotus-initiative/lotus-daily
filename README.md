# lotus-daily

[![Release](https://img.shields.io/github/v/release/lotus-initiative/lotus-daily)](https://github.com/lotus-initiative/lotus-daily/releases)
[![Build
status](https://img.shields.io/github/actions/workflow/status/lotus-initiative/lotus-daily/main.yml?branch=main)](https://github.com/lotus-initiative/lotus-daily/actions/workflows/main.yml?query=branch%3Amain)
[![codecov](https://codecov.io/gh/lotus-initiative/lotus-daily/branch/main/graph/badge.svg)](https://codecov.io/gh/lotus-initiative/lotus-daily)
[![Commit
activity](https://img.shields.io/github/commit-activity/m/lotus-initiative/lotus-daily)](https://github.com/lotus-initiative/lotus-daily/commits/main)
[![License](https://img.shields.io/github/license/lotus-initiative/lotus-daily)](https://github.com/lotus-initiative/lotus-daily/blob/main/LICENSE)

This is a Python bot for the
[`@daily_lotus`](https://mastodon.social/@daily_lotus) account on Mastodon.

- **GitHub repository**: <https://github.com/lotus-initiative/lotus-daily/>
- **Documentation**: <https://lotus-initiative.github.io/lotus-daily/>

# 🤖 daily_lotus bot

**daily_lotus** is a Python bot that toots daily natural products occurrences on
Mastodon, via the account
[`@daily_lotus`](https://mastodon.social/@daily_lotus). It highlights natural
compounds found in plants, fungi, bacteria, or animals --- and includes Wikidata
references and visual structure depictions.

The aim is to raise awareness on the hidden chemical diversity of life on Earth
and the importance of open data in bio- and chemodiversity research.

We also expect that by putting side by side the structure of the molecule, a
picture of the taxon, and the references backing up the occurrence, this can
serve as an entry point for researchers willing to contribute to the LOTUS
Initiative and edit Wikidata.

The bot is part of the [LOTUS Initiative](https://lotus.nprod.net/) and is
powered by [Wikidata](https://www.wikidata.org/wiki/Wikidata:Main_Page) (queried
via [QLever](https://qlever.dev/wikidata) with automatic fallback to
[WDQS](https://query.wikidata.org/)) and the [Cheminformatics
Microservice](https://docs.api.naturalproducts.net/). The bot is designed to be
run daily, but you can also run it manually or in `--dry-run` mode to preview
the output without posting.

## 🛠️ What does it do?

Every day at 8:00 AM, the bot:

1. Selects a random molecule from [Wikidata](https://www.wikidata.org/wiki/)
   that:
   - Has a known SMILES structure (`P2017` isomeric preferred, falling back to
     `P233` canonical)
   - Is associated with a specific taxon through the found-in-taxon property
     ([P703](https://www.wikidata.org/wiki/Property:P703))
   - Has a valid reference documenting this occurrence (`P248`)

2. Fetches:
   - The molecule's name and its SMILES structure. The structure is depicted via
     the [Cheminformatics Microservice](https://docs.api.naturalproducts.net/)
   - The taxon name (e.g., *Curcuma longa*) and its associated image via
     `taxonLabel` and [P18](https://www.wikidata.org/wiki/Property:P18)
   - The scientific publication supporting the occurrence via `P248`
   - The taxon kingdom via [P171](https://www.wikidata.org/wiki/Property:P171)
     searching for Plantae ([Q756](https://www.wikidata.org/wiki/Q756)), Fungi
     ([Q764](https://www.wikidata.org/wiki/Q764)), Animalia
     ([Q729](https://www.wikidata.org/wiki/Q729)), or Bacteria
     ([Q10876](https://www.wikidata.org/wiki/Q10876)), selecting the
     corresponding emoji for the post

3. Posts a formatted message (with image alt text and an occurrence counter) to
   Mastodon:
   - 🧪 Molecule
   - 🌿, 🍄, 🐛, or 🦠 depending on the organism's kingdom
   - 📚 Reference
   - ✏️ A link inviting readers to curate the occurrence on Wikidata
   - ⏳ An occurrence counter and estimated days/years remaining in LOTUS

## 🗒️ Example Post

```text
📣 Natural Product Occurrence of the Day

🧪 (-)-verbenone [https://www.wikidata.org/wiki/Q6535827] is a molecule
found in a 🌿 plant, Thymus camphoratus [https://www.wikidata.org/wiki/Q145377]
📚 according to: Composition and infraspecific variability of essential oil from Thymus camphoratus [https://www.wikidata.org/wiki/Q58423750]

✏️ This occurrence is available for curation on Wikidata [https://www.wikidata.org/wiki/Q6535827#P703]. If you spot an error, feel free to improve it!

⏳ Occurrence #1 / 522,000 (~1,429 yrs left)
#DailyNP #OpenScience #LOTUS #Wikidata #LinkedOpenData
```

## 🚀 How to run it

### 1. Install dependencies

Install dependencies using [uv](https://github.com/astral-sh/uv):

```bash
uv sync
```

### 2. Setting up a Mastodon bot account

1. **Create a dedicated Mastodon account** on an instance whose rules allow
   automated bots (for example `mastodon.social` or a community instance).
2. **Mark the account as automated**: in Mastodon, go to **Preferences → Profile
   → Appearance**, enable **"This is an automated account"**, and save changes.
3. **Register an application and get an access token**:
   - Go to **Preferences → Development → New application**.
   - Give the app a name (e.g., `daily-lotus`) and select at least these scopes:
     - `read:accounts`
     - `read:statuses`
     - `write:media`
     - `write:statuses`
   - Click **Submit**, open the newly created application, and copy **Your
     access token**.
   - *(Alternative without 2FA)*: You can edit `register_app.py` and run
     `uv run python register_app.py` to generate `clientcred.secret` and
     `usercred.secret`.

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your Mastodon credentials:

```bash
cp .env.example .env
```

```env
MASTODON_API_BASE_URL=https://mastodon.social
MASTODON_ACCESS_TOKEN=your_mastodon_access_token
```

Optional variables in `.env.example` let you override the primary SPARQL
endpoint/timeout (`SPARQL_ENDPOINT`, `SPARQL_TIMEOUT`) and rotating log settings
(`LOTUS_LOG_DIR`, `LOTUS_LOG_LEVEL`, `LOTUS_LOG_MAX_BYTES`,
`LOTUS_LOG_BACKUP_COUNT`).

### 4. Test and run manually

Preview a post without publishing to Mastodon:

```bash
uv run python run_bot.py --dry-run
```

Optionally send a one-line test toot to verify credentials:

```bash
uv run python test_toot.py
```

Publish a real daily post:

```bash
uv run python run_bot.py
```

#### Candidate cache & robustness options

Generate a local `candidates.json` cache (useful for avoiding repeated
full-graph candidate queries):

```bash
uv run python -m daily_lotus.generate_candidate_cache
uv run python run_bot.py --use-cache
```

If `candidates.json` is missing or unreadable, `--use-cache` automatically falls
back to a live SPARQL query. Both `get_candidate_qids` and
`get_molecule_details` query QLever first and fall back to WDQS with
exponential-backoff retries (`--max-retries`, `--retry-delay`,
`--max-candidate-attempts`). If the daily run reports an unconfirmed Mastodon
status, check the account for the post before rerunning the job.

#### LinkedIn cross-posting

For a LinkedIn Page, request [Community Management API
access](https://learn.microsoft.com/en-us/linkedin/marketing/community-management-app-review).
Use the [LinkedIn token
generator](https://www.linkedin.com/developers/tools/oauth/token-generator) with
`w_organization_social` while signed in as a Page admin.

Set `LINKEDIN_ACCESS_TOKEN` and `LINKEDIN_AUTHOR_URN` in the deployment's
private `.env`. LinkedIn receives a copy only after a public Mastodon post is
confirmed and saved, and the copy links to the original. Use `--no-linkedin` to
publish only on Mastodon or `--dry-run` to preview both without publishing.

#### Thematic posts

List all built-in themes and weekday schedules:

```bash
uv run python run_bot.py --list-themes
```

- Monday: `alkaloid` (`#MondayAlkaloid`)
- Tuesday: `insect` (`#InsectTuesday`)
- Wednesday: `big-fat-np` (`#BigFatNP`, molecular mass >= 800 Da)
- Thursday: `freshest` (`#FreshestNP`, recent publications)
- On-demand kingdom themes: `plant`, `fungi`, `bacteria`, `animal`

Run with a specific theme or let the bot select automatically by UTC weekday:

```bash
uv run python run_bot.py --theme insect --dry-run
uv run python run_bot.py --auto-theme
```

By default, if a thematic query returns no postable candidates, the bot falls
back to an unthemed daily occurrence unless `--strict-theme` is passed.

#### Occurrence counter

Print the total LOTUS occurrences on Wikidata, posted count, and remaining
days/years:

```bash
uv run python run_bot.py --counter
uv run python -m daily_lotus.counter --refresh --json
```

Pass `--no-counter` to `run_bot.py` to omit the counter line from the daily
post. The counter line is also dropped first whenever a post approaches
Mastodon's 500-character limit.

### 5. Automate daily posting & edit monitoring

Schedule daily runs and Wikidata edit monitoring via `crontab -e`:

```cron
0 8 * * * cd /full/path/to/lotus-daily && uv run python run_bot.py --auto-theme --use-cache
*/5 * * * * cd /full/path/to/lotus-daily && uv run python -m daily_lotus.check_edits
```

Or use the wrapper script `./run_check_edits.sh`. `check_edits.py` tracks label
changes, `P703` statement removals, and SMILES (`P2017`/`P233`) updates since
the last check, replying in the original thread only when a Wikidata editor
(username or IP) is identified.

### 6. Logging & log rotation

CLI scripts write rotating UTF-8 logs to `logs/` (5 MiB per file, 5 backups by
default). You can override paths and rotation limits via `--log-file`,
`--no-file-log`, `--max-log-bytes`, `--log-backup-count`, or `LOTUS_LOG_*`
environment variables. An example system configuration is also provided in
`deploy/lotus-daily.logrotate`.

### 7. Purging historical unsigned edit toots

To preview and optionally delete historical unsigned edit notifications on the
bot account:

```bash
# Dry-run preview (default, makes no changes)
uv run python -m daily_lotus.purge_unsigned_edits

# Delete matched unsigned edit toots (never touches original daily posts or signed edits)
uv run python -m daily_lotus.purge_unsigned_edits --execute --limit 50
```

## Roadmap

- [x] Add a command line interface (CLI) to run the bot with thematic parameters
  (`--theme`, `--auto-theme`, `--list-themes`)
- [ ] Display 3D structures
- [x] Check randomization of the input molecule selection (dropped initial
  limit)
- [x] Add a direct link to the found-in-taxon property (`#P703`) on Wikidata
- [x] Add relevant `#` hashtags
- [ ] Prepare a Streamlit app to display the same information without waiting
  for the daily run

## 🧬 Credits & contribution

This bot is powered by:

- [Wikidata](https://www.wikidata.org/wiki/Wikidata:Main_Page) --- the free
  knowledge base that anyone can edit
- [The LOTUS Initiative](https://lotus.nprod.net/) --- a collaborative effort to
  curate natural product occurrences ([LOTUS
  paper](https://doi.org/10.7554%2FELIFE.70780))
- [QLever](https://qlever.dev/wikidata) and [WDQS](https://query.wikidata.org/) ---
  SPARQL query endpoints
- [Cheminformatics Microservice](https://docs.api.naturalproducts.net/)
- [Mastodon.py](https://mastodonpy.readthedocs.io/en/stable/) --- a Python
  wrapper for the Mastodon API

Made with ❤️ by researchers committed to open data, chemistry, and biodiversity.
Contributions are welcome --- please check [CONTRIBUTING.md](CONTRIBUTING.md)
and open an issue or pull request!

--------------------------------------------------------------------------------

Repository initiated with
[fpgmaas/cookiecutter-uv](https://github.com/fpgmaas/cookiecutter-uv).
