"""Local journal and exclusive run lock. Does not interact with LinkedIn."""

import calendar
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1] / "data" / "local" / "linkedin-network"
STATE = ROOT / "state.json"
LOCK = ROOT / "cycle.lock"
TZ = ZoneInfo("America/Sao_Paulo")


def now():
    return datetime.now(TZ)


def save(s):
    tmp = STATE.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE)


def normalized(url):
    p = urlsplit(url)
    assert p.hostname and (p.hostname == "linkedin.com" or p.hostname.endswith(".linkedin.com"))
    assert p.path.startswith("/in/")
    return urlunsplit(("https", "www.linkedin.com", p.path.rstrip("/") + "/", "", ""))


def identity(url):
    return unquote(urlsplit(normalized(url)).path.split("/")[2]).casefold()


def owner(run):
    assert (LOCK / "owner").read_text().strip() == run, "Another cycle owns the lock"


def account_gate(s, data):
    proof = s.get("account_type_confirmation", {})
    assert proof.get("status") == "confirmed" and proof.get("type") in ("premium", "standard"), (
        "Explicit user confirmation of account type required"
    )
    assert proof.get("profile_url") == "https://www.linkedin.com/in/malkhasyan/"
    assert proof.get("source_kind") == "user_message" and proof.get("response_text"), (
        "UI evidence alone is not the required user answer"
    )
    assert (
        data.get("account_url") == proof["profile_url"] and data.get("ui_account_verified") is True
    ), "Verify the current browser account before this action"


def next_day_nine(t):
    return (t + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)


def calendar_month_cutoff(t):
    year, month = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    return t.replace(year=year, month=month, day=min(t.day, calendar.monthrange(year, month)[1]))


def cleanup_date_proof(s, data, t):
    assert data.get("date_source") in (
        "journal_confirmed_send",
        "ui_exact_timestamp",
        "ui_exact_date",
    ), "Rounded ages are not a reliable date"
    assert data.get("dates_consistent") is True, "Unresolved date conflict"
    c = s["contacts"].get(normalized(data["url"]), {})
    cutoff = calendar_month_cutoff(t)
    if data.get("sent_at"):
        sent = datetime.fromisoformat(data["sent_at"])
        assert sent.tzinfo is not None
        sent = sent.astimezone(TZ)
        if c.get("sent_at"):
            assert sent == datetime.fromisoformat(c["sent_at"]).astimezone(TZ), (
                "Date conflicts with journal"
            )
        if data["date_source"] == "journal_confirmed_send":
            assert c.get("confirmation") and c.get("sent_at"), (
                "No confirmed send timestamp in journal"
            )
        assert sent < cutoff, "Invitation is not strictly older than one calendar month"
        return {
            "sent_at": sent.isoformat(),
            "cutoff": cutoff.isoformat(),
            "date_source": data["date_source"],
        }
    assert data.get("sent_date") and data["date_source"] == "ui_exact_date", (
        "Exact date or timestamp required"
    )
    sent_date = datetime.strptime(data["sent_date"], "%Y-%m-%d").date()
    if c.get("sent_at"):
        assert sent_date == datetime.fromisoformat(c["sent_at"]).astimezone(TZ).date(), (
            "Date conflicts with journal"
        )
    assert sent_date < cutoff.date(), "Date-only invitation must precede the cutoff calendar date"
    return {
        "sent_date": sent_date.isoformat(),
        "cutoff": cutoff.isoformat(),
        "date_source": data["date_source"],
    }


def activate_planned_stage(s, t):
    pending = s.get("pending_stage_change")
    if not pending:
        return
    effective = datetime.strptime(pending["effective_from"], "%Y-%m-%d").date()
    if t.date() < effective:
        return
    valid = (
        s["mode"] == "active"
        and not s["blockers"]
        and t.date() == effective
        and all(
            s["days"].get((effective - timedelta(days=n)).isoformat(), {}).get("success")
            and s["days"][(effective - timedelta(days=n)).isoformat()]["target"] == pending["from"]
            for n in range(1, 8)
        )
    )
    if valid:
        s["stage"] = s["daily_target"] = pending["to"]
        s["successful_streak"] = 0
        s["streak_started_on"] = effective.isoformat()
        s["transitions"].append({**pending, "at": t.isoformat(), "status": "activated"})
    else:
        s["transitions"].append(
            {
                **pending,
                "at": t.isoformat(),
                "status": "cancelled",
                "reason": "Missed activation date or invalid recent series; no stale promotion",
            }
        )
    s["pending_stage_change"] = None


def main():
    command = sys.argv[1]
    s = json.loads(STATE.read_text())
    if command == "status":
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    run = sys.argv[2]
    if command == "begin":
        LOCK.mkdir()  # Atomic; never take over a stale lock without reconciliation.
        (LOCK / "owner").write_text(run)
        kind = json.loads(sys.argv[3]).get("kind", "daily") if len(sys.argv) > 3 else "daily"
        if kind == "daily":
            activate_planned_stage(s, now())
        s["cycles"].append(
            {
                "id": run,
                "kind": kind,
                "started_at": now().isoformat(),
                "status": "running",
                "queries": 0,
                "checks": 0,
            }
        )
        save(s)
        print("LOCK ACQUIRED: " + run)
        return
    owner(run)
    cycle = next(c for c in s["cycles"] if c["id"] == run)
    if command == "end":
        assert not s["unresolved_attempt"] and not s.get("unresolved_withdrawal"), (
            "Resolve action before ending"
        )
        cycle.update(status="finished", ended_at=now().isoformat())
        save(s)
        (LOCK / "owner").unlink()
        LOCK.rmdir()
        print("LOCK RELEASED")
        return
    data = json.loads(sys.argv[3])
    if command == "patch":
        assert not any(
            k in data
            for k in (
                "contacts",
                "days",
                "last_outbound_at",
                "unresolved_attempt",
                "unresolved_withdrawal",
            )
        ), "Use guarded commands for action records"
        s.update(data)
    elif command == "query":
        budget = {5: 12, 10: 18, 13: 24}[s["stage"]]
        assert cycle["queries"] < budget, "Google query budget reached"
        data["at"] = now().isoformat()
        s["query_history"].append(data)
        cycle["queries"] += 1
        s["cursors"][data["group"]] = data["next_id"]
    elif command == "contact":
        url = normalized(data["url"])
        old = s["contacts"].get(url, {})
        assert old.get("status") not in ("отправлен", "pending ранее", "уже в сети", "отозван"), (
            "Do not overwrite prior outbound/network record"
        )
        assert not any(
            identity(u) == identity(url) and c.get("status") == "отозван"
            for u, c in s["contacts"].items()
        ), "Withdrawn contact; separate user decision required even for a locale URL variant"
        if data.get("profile_check"):
            assert cycle["checks"] < {5: 15, 10: 30, 13: 40}[s["stage"]], "Profile budget reached"
            cycle["checks"] += 1
        data["url"] = url
        data["checked_at"] = now().isoformat()
        s["contacts"][url] = {**old, **data}
        if data["status"] == "проверен" and url not in s["queue"]:
            s["queue"].append(url)
    elif command == "evidence":
        url = normalized(data.pop("url"))
        assert set(data) <= {"current_role", "company", "source", "verification", "proof_file"}, (
            "Evidence cannot alter outbound status or counts"
        )
        c = s["contacts"][url]
        c.setdefault("verification_history", []).append({"at": now().isoformat(), **data})
        for k in ("current_role", "company", "source"):
            if k in data:
                c[k] = data[k]
    elif command == "reserve":
        account_gate(s, data)
        t = now()
        assert cycle.get("kind", "daily") not in ("update", "cleanup"), (
            "This cycle does not send invitations"
        )
        if cycle.get("kind", "daily") == "daily":
            assert t >= t.replace(hour=9, minute=0, second=0, microsecond=0), (
                "Scheduled invitations cannot start before 09:00 Sao Paulo"
            )
        probe = data.get("limit_probe") is True
        assert not s["blockers"], "Sending blocked"
        assert s["mode"] == "active" or (s["mode"] == "invite_limit_paused" and probe), (
            "Sending paused"
        )
        assert not s["unresolved_attempt"] and not s.get("unresolved_withdrawal"), (
            "Unknown previous result; reconcile first"
        )
        if probe:
            assert s["mode"] == "invite_limit_paused" and s.get("next_limit_check_at")
            assert t >= datetime.fromisoformat(s["next_limit_check_at"]), "Limit probe not due"
            assert s.get("last_limit_probe_day") != t.date().isoformat(), (
                "Only one limit probe per local day"
            )
        url = normalized(data["url"])
        c = s["contacts"][url]
        assert c["status"] == "проверен" and url not in s["stoplist"], "Contact not eligible"
        assert not any(
            identity(u) == identity(url) and prior.get("status") == "отозван"
            for u, prior in s["contacts"].items()
        ), "Previously withdrawn; do not reinvite automatically"
        assert data["account_url"] == s["account"]["url"], "Wrong account"
        assert data["ui_no_pending_or_connection"] is True
        assert c.get("brazil_evidence") and c.get("current_role") and c.get("company")
        day = s["days"].setdefault(
            t.date().isoformat(),
            {"target": s["daily_target"], "sent": 0, "success": False, "unknown": False},
        )
        cap = min(day["target"], s["daily_target"], 5 if probe else 13)
        assert day["sent"] < cap, "Daily target reached"
        if s["last_outbound_at"]:
            elapsed = (t - datetime.fromisoformat(s["last_outbound_at"])).total_seconds()
            assert elapsed >= 240, "Wait %.0f more seconds" % (240 - elapsed)
        if probe:
            s["last_limit_probe_day"] = t.date().isoformat()
            s["next_limit_check_at"] = next_day_nine(t).isoformat()
        s["unresolved_attempt"] = {
            "url": url,
            "at": t.isoformat(),
            "run": run,
            "limit_probe": probe,
        }
        day["unknown"] = True
    elif command == "limit-hit":
        t = now()
        assert data.get("message") and data.get("ui_confirmed_not_sent") is True
        a = s["unresolved_attempt"]
        if a:
            assert a["run"] == run and a["url"] == normalized(data["url"])
            s["contacts"][a["url"]].setdefault("attempt_history", []).append(
                {"at": t.isoformat(), "result": "invite_limit", "message": data["message"]}
            )
            s["days"][datetime.fromisoformat(a["at"]).date().isoformat()]["unknown"] = False
            s["unresolved_attempt"] = None
        s["mode"] = "invite_limit_paused"
        s["pause_started_at"] = s.get("pause_started_at") or t.isoformat()
        s["last_limit_failure_at"] = t.isoformat()
        s["last_limit_probe_day"] = t.date().isoformat()
        explicit = data.get("linkedin_recovery_at")
        if explicit:
            explicit = datetime.fromisoformat(explicit)
            assert explicit.tzinfo is not None, "LinkedIn recovery time must include timezone"
            explicit = explicit.astimezone(TZ)
        s["linkedin_limit_recovery_at"] = (
            explicit.isoformat() if explicit and explicit > t else None
        )
        s["next_limit_check_at"] = (
            explicit if explicit and explicit > t else next_day_nine(t)
        ).isoformat()
        s.setdefault("limit_history", []).append(
            {
                "at": t.isoformat(),
                "message": data["message"],
                "sent_today": s["days"].get(t.date().isoformat(), {}).get("sent", 0),
                "target": s["daily_target"],
                "next_limit_check_at": s["next_limit_check_at"],
            }
        )
        s["successful_streak"] = 0
        s["pending_stage_change"] = None
    elif command == "withdraw-reserve":
        account_gate(s, data)
        assert not s["unresolved_attempt"] and not s.get("unresolved_withdrawal"), (
            "Resolve prior result first"
        )
        assert s["mode"] != "security_blocked" and data.get("no_security_warning") is True
        assert data.get("ui_pending") is True, "Not currently pending"
        t = now()
        assert s.get("next_cleanup_at") and t >= datetime.fromisoformat(s["next_cleanup_at"]), (
            "Scheduled cleanup is not due"
        )
        proof = cleanup_date_proof(s, data, t)
        url = normalized(data["url"])
        assert s["contacts"].get(url, {}).get("status") not in ("отозван", "уже в сети")
        s["unresolved_withdrawal"] = {
            "run": run,
            "url": url,
            "name": data["name"],
            "at": t.isoformat(),
            "date_proof": proof,
        }
    elif command == "withdraw-confirm":
        a = s.get("unresolved_withdrawal")
        assert a and a["run"] == run and a["url"] == normalized(data["url"])
        assert data.get("ui_success_text") and data.get("pending_removed") is True
        t = now().isoformat()
        c = s["contacts"].setdefault(a["url"], {"url": a["url"], "name": a["name"]})
        c.update(
            status="отозван",
            withdrawn_at=t,
            do_not_reinvite_automatically=True,
            reinvite_requires_user_decision=True,
        )
        event = {**a, "withdrawn_at": t, "confirmation": data, "run_id": run}
        s.setdefault("withdrawal_events", []).append(event)
        s["queue"] = [u for u in s["queue"] if identity(u) != identity(a["url"])]
        s["unresolved_withdrawal"] = None
    elif command == "cleanup-finalize":
        account_gate(s, data)
        assert data.get("full_coverage") is True and not s.get("unresolved_withdrawal")
        t = now()
        assert s.get("next_cleanup_at") and t >= datetime.fromisoformat(s["next_cleanup_at"]), (
            "Cleanup not due"
        )
        s["last_cleanup_at"] = t.isoformat()
        s["next_cleanup_at"] = (
            (t + timedelta(days=21)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
        )
        s.setdefault("cleanup_history", []).append(
            {"at": t.isoformat(), "run_id": run, **data, "next_cleanup_at": s["next_cleanup_at"]}
        )
        cycle["cleanup_result"] = data
    elif command == "finalize":
        assert not s["unresolved_attempt"], "Resolve unknown result first"
        today = now().date()
        key = today.isoformat()
        day = s["days"].setdefault(key, {"target": s["daily_target"], "sent": 0, "unknown": False})
        day["success"] = bool(
            day["sent"] == day["target"]
            and not day["unknown"]
            and s["mode"] == "active"
            and not s["blockers"]
            and not data.get("unresolved_technical_failure")
        )
        day["reason"] = data.get("reason", "")
        streak = 0
        d = today
        while True:
            prior = s["days"].get(d.isoformat(), {})
            if (
                not prior.get("success")
                or prior.get("target") != s["stage"]
                or d.isoformat() < s.get("streak_started_on", "0001-01-01")
            ):
                break
            streak += 1
            d -= timedelta(days=1)
        s["successful_streak"] = streak
        s["successful_days"] = [d for d, v in sorted(s["days"].items()) if v.get("success")]
        if streak >= 7 and s["stage"] in (5, 10):
            old = s["stage"]
            s["pending_stage_change"] = {
                "from": old,
                "to": 10 if old == 5 else 13,
                "effective_from": (today + timedelta(days=1)).isoformat(),
            }
        cycle["result"] = dict(day)
    elif command == "confirm":
        a = s["unresolved_attempt"]
        assert a and a["run"] == run and a["url"] == normalized(data["url"])
        assert data["toast"] and data["pending_evidence"], "Need toast and Pending UI evidence"
        t = now()
        if a.get("limit_probe"):
            old = s["stage"]
            s["mode"] = "active"
            s["stage"] = s["daily_target"] = 5
            s["successful_streak"] = 0
            s["streak_started_on"] = t.date().isoformat()
            s["pending_stage_change"] = None
            s["transitions"].append(
                {
                    "at": t.isoformat(),
                    "from": old,
                    "to": 5,
                    "effective_from": t.date().isoformat(),
                    "reason": "confirmed_limit_probe_resume",
                }
            )
            s["pause_started_at"] = None
            s["next_limit_check_at"] = None
            s["days"][datetime.fromisoformat(a["at"]).date().isoformat()]["target"] = 5
        c = s["contacts"][a["url"]]
        c.update(status="отправлен", sent_at=t.isoformat(), confirmation=data)
        day = s["days"][datetime.fromisoformat(a["at"]).date().isoformat()]
        day["sent"] += 1
        day["unknown"] = False
        s["last_outbound_at"] = t.isoformat()
        s["unresolved_attempt"] = None
        s["queue"] = [u for u in s["queue"] if u != a["url"]]
    else:
        raise ValueError(command)
    save(s)
    print(
        json.dumps(
            {
                "command": command,
                "at": now().isoformat(),
                "days": s["days"],
                "last_outbound_at": s["last_outbound_at"],
                "unresolved_attempt": s["unresolved_attempt"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
