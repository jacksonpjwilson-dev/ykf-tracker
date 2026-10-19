"""GitHub side effects, all non-fatal: publish live.json to a single-commit
'data' branch, and open one issue per poll that produced alerts.
Auth comes from the box's gh login (git credential helper); no tokens in files."""
import json
import os
import shutil
import subprocess

PUBLIC_ALERT_FIELDS = ("tail", "type", "distance_nm", "altitude_ft", "eta", "time")


def _run(cmd, cwd=None, inp=None, timeout=90):
    r = subprocess.run(cmd, cwd=cwd, input=inp, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(cmd[:3]), (r.stderr or r.stdout).strip()[:300]))
    return r.stdout


def publish_live(cfg, doc):
    """Force-push live.json as the only commit on the data branch."""
    p = cfg["publish"]
    d = cfg["paths"]["publish_dir"]
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    with open(os.path.join(d, "live.json"), "w") as f:
        json.dump(doc, f, indent=1, default=str)
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write("Machine-written live data for the YKF arrivals page. Force-pushed as a single commit; no history.\n")
    g = ["git", "-c", "user.name=" + p["git_name"], "-c", "user.email=" + p["git_email"]]
    _run(["git", "init", "-q", "-b", p["branch"]], cwd=d)
    _run(["git", "add", "-A"], cwd=d)
    _run(g + ["commit", "-q", "-m", "live data " + str(doc.get("generated"))], cwd=d)
    _run(["git", "push", "-q", "-f", "https://github.com/%s.git" % p["repo"], "HEAD:refs/heads/" + p["branch"]], cwd=d)


def public_alert(a):
    return {k: a.get(k) for k in PUBLIC_ALERT_FIELDS}


def issue_title(alerts):
    if len(alerts) == 1:
        return alerts[0]["text"][:250]
    return ("%d YKF arrivals: " % len(alerts) + " | ".join(a["text"] for a in alerts))[:250]


def issue_body(alerts):
    pub = [public_alert(a) for a in alerts]
    lines = ["YKF arrival alert%s (%d):" % ("s" if len(pub) > 1 else "", len(pub)), ""]
    for a in alerts:
        lines.append("- " + a["text"])
    lines += ["", "```json", json.dumps(pub if len(pub) > 1 else pub[0], indent=1), "```", "",
              "Written by the YKF tracker script. Tower closes this after delivery."]
    return "\n".join(lines)


def issue_cmd(cfg, title):
    i = cfg["issues"]
    return ["gh", "issue", "create", "-R", i["repo"], "--title", title, "--body-file", "-",
            "--label", i["label"], "--assignee", i["assignee"]]


def create_issue(cfg, title, body, dry_run=False):
    """Returns (number, url). dry_run: print the exact gh command + body, create nothing.
    Raises on failure (caller treats as non-fatal)."""
    cmd = issue_cmd(cfg, title)
    if dry_run or cfg["issues"].get("dry_run"):
        print("DRY RUN, would run:", " ".join(repr(c) if " " in c else c for c in cmd))
        print("--- body ---\n" + body + "\n------------")
        return None, None
    out = _run(cmd, inp=body, timeout=60)
    url = out.strip().splitlines()[-1]
    return int(url.rstrip("/").split("/")[-1]), url
