# Distribution notes

This directory is the working tree of the project. It was exported from a private
working copy; there is no git history in the zip.

## First run

Requires Python 3.9 or newer. No third-party packages — the rig uses only the standard
library (`http.server`, `sqlite3`, `urllib`).

```bash
python practice/run_demo.py           # then open http://localhost:8000/dashboard
```

In a second terminal:

```bash
cd practice
python -m harness.runner             # runs 33 checks, writes practice/out/report_*.md
```

On Windows use `py` or `python` in place of `python3`.

## Where things are

| Path | What it is |
| --- | --- |
| `practice/` | the practice rig: fake PMS, fake access-control API, harness, dashboard |
| `practice/README.md` | how the rig works, endpoint by endpoint |
| `practice/harness/` | the verification harness |
| `practice/postman_collection.json` | the same calls, importable into Postman |

## Starting your own repository from this

```bash
cd ..
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```

`practice/rig.db` and `practice/out/` are generated at runtime and are already ignored.
