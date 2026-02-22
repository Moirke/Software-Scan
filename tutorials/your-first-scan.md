# Your First Scan

This tutorial walks you through running a scan using the web interface.

## Before you start

Start the application (see [running-with-gunicorn.md](running-with-gunicorn.md) for
production-like setup, or use the quick dev server):

```bash
python run-web.py
```

Open http://localhost:5000 in your browser.

---

## Step 1 — Choose a scan target

The top section of the form lets you choose what to scan. There are three options.

### Git repository (most common)

Select the **Git repository** tab and paste in a public repo URL:

```
https://github.com/owner/repo
```

The scanner will do a shallow clone automatically — you do not need git credentials
for public repositories.

### ZIP file

Select the **ZIP Upload** tab and drag a ZIP onto the drop zone, or click
**Choose a ZIP** to browse. Only `.zip` files are accepted.

This is the fastest option for scanning local code — zip your project folder first:

```bash
zip -r my-project.zip ./my-project
```

### Artifactory

Select the **Artifactory** tab, paste in the artifact or folder URL, then expand
**Authentication** to supply an API key, username/password, or leave as **None**
for public repositories.

---

## Step 2 — Choose a prohibited words source

This tells the scanner which words to look for. There are three options.

### Upload a file (easiest to start)

Select the **Upload file** tab and provide a plain text file with one word per line:

```
password
secret
api_key
TODO
FIXME
```

### Server path

Select the **Server path** tab and enter an absolute path on the machine running
the scanner:

```
/etc/scanner/prohibited_words.txt
```

You can point to a folder and the scanner will look for `prohibited_words.txt`
inside it automatically.

### Git repository

Select the **Git repository** tab and enter a URL to the file or the folder
containing it:

```
https://github.com/org/config-repo/config/words.txt
```

If you point to a folder, the scanner looks for `prohibited_words.txt` inside it.

> **Tip:** If your words file lives in the same repository as your scan target,
> the UI will detect this and show a note confirming that the config folder will
> be excluded from the scan automatically.

---

## Step 3 — Set scan options

Below the source sections you will find two options:

| Option | Default | Notes |
|---|---|---|
| Max file size (MB) | 10 | Files larger than this are skipped with a warning |
| Case sensitive | Off | Turn on to treat `Password` and `password` as different |

---

## Step 4 — Run the scan

Click **Run Scan**. The button will change to **Scanning…** while the scan runs.
For a git source you will see "Cloning repository and scanning…" — this can take
a few seconds for larger repos.

---

## Step 5 — Read the results

### No violations

A green confirmation message appears: "No prohibited words found — repository is clean."

### Violations found

A stats bar shows four counts at a glance:

- **Total** — all violations combined
- **Exact** — the word appeared as a complete token (e.g. `password = …`)
- **Partial** — the word appeared inside a larger token (e.g. `mypassword`)
- **Files affected** — number of distinct files with at least one violation

Results are grouped into two sections:

**Exact matches** are shown with a red badge and should be reviewed first — these
are the most likely genuine hits.

**Partial matches** are shown with an amber badge and may be false positives
(e.g. a variable named `updatepassword`). Review these with lower priority.

Each violation shows the file path, line number, matched word, and the full
line of content for context.

> If there are more than 100 violations, a notice appears at the bottom. Use
> **Export** to see the full list.

---

## Step 6 — Export the results

After a successful scan, a **Download PDF Report** button appears. Click it to
download a formatted report suitable for sharing or attaching to a ticket.

For a machine-readable export, use the REST API — see
[scanning-with-the-rest-api.md](scanning-with-the-rest-api.md).
